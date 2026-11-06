"""Shared HTTP settings and a streaming file download."""

import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import requests

TIMEOUT = 30
USER_AGENT = "transcription/0.1"
USER_AGENT_BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def download_file(url: str, directory: Path, stem: str) -> Path:
    """Stream `url` into directory/stem.<ext>. The extension comes from the URL path or the content type.

    The data goes to a .part file, which is renamed to the final name only when the download is complete.
    """
    print(f"downloading {url}")
    with requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True
    ) as response:
        response.raise_for_status()
        suffix = Path(urlparse(response.url).path).suffix
        if not suffix:
            content_type = response.headers.get("Content-Type", "").split(";")[0]
            suffix = mimetypes.guess_extension(content_type) or ".bin"
        path = directory / f"{stem}{suffix}"
        part = path.with_name(path.name + ".part")
        with part.open("wb") as handle:
            for block in response.iter_content(chunk_size=1 << 20):
                handle.write(block)
    part.replace(path)
    return path
