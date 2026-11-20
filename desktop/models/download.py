"""Download the files of a model from Hugging Face into the models folder, with progress, cancel and resume.

Files go into <root>/<id>.partial/, each as <name>.part until complete. A .part file left by a cancel
or a dropped connection is continued with an HTTP Range request. When all files are complete and have
the expected size, the folder is renamed to <root>/<id>.
"""

from pathlib import Path
from typing import Callable

import httpx

from desktop.models.catalog import ModelFile, ModelSpec, model_path, partial_path

HF_URL = "https://huggingface.co"
CHUNK_SIZE = 1024 * 1024
TIMEOUT_SECONDS = 30.0

Progress = Callable[[int, int], None]  # (bytes done, bytes total)


class Cancelled(Exception):
    pass


class DownloadFailed(Exception):
    pass


def file_url(base_url: str, model: ModelSpec, file: ModelFile) -> str:
    return f"{base_url}/{model.repo}/resolve/{model.revision}/{file.name}"


def fetch(client: httpx.Client, url: str, part: Path, file: ModelFile, report: Callable[[int], None],
          cancelled: Callable[[], bool], chunk_size: int) -> None:
    """Append the rest of one file to `part`. `report` gets the size of each chunk written."""
    have = part.stat().st_size if part.exists() else 0
    if have >= file.size:
        return
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code >= 400:
                raise DownloadFailed(f"{file.name}: server returned HTTP {response.status_code}")
            if have and response.status_code != 206:
                part.unlink()
                report(-have)
                have = 0
            with part.open("ab") as handle:
                for chunk in response.iter_bytes(chunk_size):
                    if cancelled():
                        raise Cancelled()
                    handle.write(chunk)
                    report(len(chunk))
    except httpx.HTTPError as error:
        raise DownloadFailed(f"{file.name}: {error}") from error


def download(model: ModelSpec, root: Path, progress: Progress, cancelled: Callable[[], bool] = lambda: False,
             base_url: str = HF_URL, chunk_size: int = CHUNK_SIZE) -> Path:
    """Download all files of `model` and return its folder. Raise Cancelled or DownloadFailed; the
    partial files stay for the next call."""
    target = model_path(model, root)
    if target.is_dir():
        return target
    partial = partial_path(model, root)
    partial.mkdir(parents=True, exist_ok=True)
    total = model.size_bytes
    done = sum(min((partial / f"{f.name}.part").stat().st_size, f.size)
               for f in model.files if (partial / f"{f.name}.part").exists())

    def report(count: int) -> None:
        nonlocal done
        done += count
        progress(min(done, total), total)

    progress(done, total)
    with httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS) as client:
        for file in model.files:
            part = partial / f"{file.name}.part"
            fetch(client, file_url(base_url, model, file), part, file, report, cancelled, chunk_size)
            size = part.stat().st_size
            if size != file.size:
                part.unlink()
                raise DownloadFailed(f"{file.name}: size {size} bytes, expected {file.size}. Try again")
    for file in model.files:
        (partial / f"{file.name}.part").rename(partial / file.name)
    partial.rename(target)
    return target
