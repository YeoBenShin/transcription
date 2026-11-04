"""Job queue: runs each submitted URL as a `python -m app` subprocess, one at a time."""

import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_LOG_CHARS = 200_000


@dataclass
class Job:
    id: str
    url: str
    args: List[str]
    status: str = "queued"  # queued | running | done | failed
    log: str = ""
    files: List[str] = field(default_factory=list)  # paths relative to outdir


class Runner:
    """Runs queued jobs one at a time, each as a `python -m app` subprocess."""

    def __init__(self, outdir: Path):
        self.outdir = outdir
        self.jobs: Dict[str, Job] = {}
        self.order: List[str] = []
        self.lock = threading.Lock()
        self.pending: "queue.Queue[Job]" = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    def submit(self, url: str, chunk_minutes: float, model: str) -> Job:
        args = [
            sys.executable, "-u", "-m", "app", url,
            "--chunk-minutes", f"{chunk_minutes:g}",
            "--outdir", str(self.outdir),
            "--model", model,
        ]
        job = Job(id=uuid.uuid4().hex[:12], url=url, args=args)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self.pending.put(job)
        return job

    def _work(self) -> None:
        while True:
            job = self.pending.get()
            job.status = "running"
            process = subprocess.Popen(
                job.args, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for chunk in iter(lambda: process.stdout.read(256), ""):
                job.log = (job.log + chunk)[-MAX_LOG_CHARS:]
            code = process.wait()
            job.files = self._files_from_log(job.log)
            job.status = "done" if code == 0 and job.files else "failed"

    def _files_from_log(self, log: str) -> List[str]:
        """Return the output paths the CLI printed, relative to outdir."""
        outdir = (PROJECT_ROOT / self.outdir).resolve()
        files = []
        for line in log.splitlines():
            if not line.startswith("  ") or not line.strip().endswith(".txt"):
                continue
            path = (PROJECT_ROOT / line.strip()).resolve()
            if path.is_relative_to(outdir) and path.is_file():
                files.append(path.relative_to(outdir).as_posix())
        return files

    def snapshot(self) -> List[dict]:
        with self.lock:
            return [self.jobs[i].__dict__ | {"args": None} for i in reversed(self.order)]

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)
