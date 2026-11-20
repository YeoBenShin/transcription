"""Model catalog and downloader of the desktop app. A local HTTP server stands in for Hugging Face."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from desktop.models import catalog, download
from desktop.models.catalog import ModelFile, ModelSpec

FILES = {"config.json": b"{}" * 10, "model.bin": bytes(range(256)) * 400}


def spec(**changes) -> ModelSpec:
    values = dict(
        id="test-model",
        title="Test",
        backend="faster-whisper",
        repo="org/test-model",
        revision="abc123",
        files=tuple(ModelFile(name, len(data)) for name, data in FILES.items()),
        note="",
    )
    values.update(changes)
    return ModelSpec(**values)


class Server:
    """Serves FILES at /<repo>/resolve/<revision>/<name>, with Range support. Records each request."""

    def __init__(self):
        self.requests = []
        self.fail_after = None  # bytes of model.bin to send before the connection drops
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append((self.path, self.headers.get("Range")))
                name = self.path.rsplit("/", 1)[-1]
                if not self.path.startswith("/org/test-model/resolve/abc123/") or name not in FILES:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = FILES[name]
                start = 0
                if self.headers.get("Range"):
                    start = int(self.headers["Range"].split("=")[1].split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if outer.fail_after is not None and name == "model.bin":
                    self.wfile.write(body[: outer.fail_after])
                    outer.fail_after = None
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


def test_download_writes_all_files_and_reports_progress(server, tmp_path):
    seen = []
    path = download.download(spec(), tmp_path, progress=lambda done, total: seen.append((done, total)), base_url=server.url)
    assert path == tmp_path / "test-model"
    assert {p.name: p.read_bytes() for p in path.iterdir()} == FILES
    total = sum(len(d) for d in FILES.values())
    assert seen[-1] == (total, total)
    assert all(b >= a for (a, _), (b, _) in zip(seen, seen[1:]))
    assert catalog.is_installed(spec(), tmp_path)


def test_cancel_keeps_partial_and_next_download_resumes(server, tmp_path):
    calls = {"n": 0}

    def cancel_once():
        calls["n"] += 1
        return calls["n"] == 3

    with pytest.raises(download.Cancelled):
        download.download(spec(), tmp_path, progress=lambda d, t: None, cancelled=cancel_once, base_url=server.url,
                          chunk_size=1024)
    assert not catalog.is_installed(spec(), tmp_path)
    server.requests.clear()
    path = download.download(spec(), tmp_path, progress=lambda d, t: None, base_url=server.url)
    assert (path / "model.bin").read_bytes() == FILES["model.bin"]
    assert any(r[1] for r in server.requests), "the second download asks for the rest with a Range header"


def test_dropped_connection_fails_and_resume_completes(server, tmp_path):
    server.fail_after = 5000
    with pytest.raises(download.DownloadFailed):
        download.download(spec(), tmp_path, progress=lambda d, t: None, base_url=server.url)
    path = download.download(spec(), tmp_path, progress=lambda d, t: None, base_url=server.url)
    assert (path / "model.bin").read_bytes() == FILES["model.bin"]


def test_http_error_fails(server, tmp_path):
    with pytest.raises(download.DownloadFailed, match="404"):
        download.download(spec(revision="missing"), tmp_path, progress=lambda d, t: None, base_url=server.url)


def test_wrong_size_fails(server, tmp_path):
    bad = spec(files=(ModelFile("config.json", 3),))
    with pytest.raises(download.DownloadFailed, match="size"):
        download.download(bad, tmp_path, progress=lambda d, t: None, base_url=server.url)
    assert not catalog.is_installed(bad, tmp_path)


def test_delete_removes_installed_and_partial(server, tmp_path):
    download.download(spec(), tmp_path, progress=lambda d, t: None, base_url=server.url)
    catalog.delete(spec(), tmp_path)
    assert not (tmp_path / "test-model").exists()


def test_catalog_hides_parakeet_off_apple_silicon():
    on_mac = {m.backend for m in catalog.available("darwin", "arm64")}
    on_windows = {m.backend for m in catalog.available("win32", "AMD64")}
    assert on_mac == {"faster-whisper", "parakeet-mlx"}
    assert on_windows == {"faster-whisper"}
    assert {"whisper-tiny", "whisper-base", "whisper-small", "whisper-medium", "whisper-large-v3",
            "whisper-large-v3-turbo"} <= {m.id for m in catalog.available("win32", "AMD64")}


def test_catalog_ids_are_unique_and_sizes_add_up():
    ids = [m.id for m in catalog.CATALOG]
    assert len(ids) == len(set(ids))
    for model in catalog.CATALOG:
        assert model.size_bytes == sum(f.size for f in model.files) > 0
