"""HTTP server: serves the frontend, the jobs API, and transcript files from outdir."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from app.web.backend.runner import PROJECT_ROOT, Runner

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


def make_handler(runner: Runner):
    outdir = (PROJECT_ROOT / runner.outdir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, content_type: str, download: str = "") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if download:
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download)}")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data).encode(), "application/json")

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                self._send(200, (FRONTEND_DIR / "index.html").read_bytes(), STATIC_TYPES[".html"])
            elif path.startswith("/static/"):
                target = (FRONTEND_DIR / unquote(path[len("/static/"):])).resolve()
                if not target.is_relative_to(FRONTEND_DIR) or not target.is_file() or target.suffix not in STATIC_TYPES:
                    self._json(404, {"error": "not found"})
                    return
                self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])
            elif path == "/api/jobs":
                self._json(200, runner.snapshot())
            elif path.startswith("/files/"):
                target = (outdir / unquote(path[len("/files/"):])).resolve()
                if not target.is_relative_to(outdir) or not target.is_file() or target.suffix != ".txt":
                    self._json(404, {"error": "not found"})
                    return
                download = f"{target.parent.name}_{target.name}" if "download" in query else ""
                self._send(200, target.read_bytes(), "text/plain; charset=utf-8", download)
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/jobs":
                self._json(404, {"error": "not found"})
                return
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                url = str(data.get("url", "")).strip()
                chunk_minutes = float(data.get("chunk_minutes", 4))
                model = str(data.get("model", "small")).strip() or "small"
            except (ValueError, TypeError):
                self._json(400, {"error": "invalid request"})
                return
            if not url.startswith(("http://", "https://")):
                self._json(400, {"error": "enter a full http(s) URL"})
                return
            if chunk_minutes <= 0:
                self._json(400, {"error": "chunk minutes must be more than 0"})
                return
            job = runner.submit(url, chunk_minutes, model)
            self._json(201, {"id": job.id})

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.web", description="Local web interface for the CLI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(Runner(args.outdir)))
    print(f"open http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
