"""Job queue: runs each submitted URL as a `python -m app` subprocess, one at a time."""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.models import TEMP_DIRNAME
from app.stages.common import temp_files

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_LOG_CHARS = 200_000
LOG_SCAN_CHARS = 1024  # longer than one 'temp name: <name>' line plus one read
ACTIVE = ("queued", "running")
ENDED = ("stopped", "failed")  # statuses that can continue or delete


@dataclass
class Job:
    id: str
    url: str
    args: List[str]
    status: str = "queued"  # queued | running | done | failed | stopped
    log: str = ""
    files: List[str] = field(default_factory=list)  # paths relative to outdir
    temp_name: str = ""  # <name> of the job's work files in <outdir>/temp/, from the CLI log
    started: Optional[float] = None  # time.monotonic() when status became running
    finished: Optional[float] = None  # time.monotonic() when status became done, failed or stopped
    process: Optional[subprocess.Popen] = None
    stop_requested: bool = False


class Runner:
    """Runs queued jobs one at a time, each as a `python -m app` subprocess."""

    def __init__(self, outdir: Path):
        self.outdir = outdir
        self.jobs: Dict[str, Job] = {}
        self.order: List[str] = []
        self.lock = threading.Lock()
        self.pending: "queue.Queue[Job]" = queue.Queue()
        threading.Thread(target=self._work, daemon=True).start()

    @property
    def temp_dir(self) -> Path:
        return (PROJECT_ROOT / self.outdir).resolve() / TEMP_DIRNAME

    def submit(self, url: str, model: str) -> Job:
        args = [
            sys.executable, "-u", "-m", "app", url,
            "--outdir", str(self.outdir),
            "--model", model,
        ]
        job = Job(id=uuid.uuid4().hex[:12], url=url, args=args)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self.pending.put(job)
        return job

    def stop(self, job_id: str) -> Optional[str]:
        """Stop a queued or running job. Its temp files stay. Return an error message, or None."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return "no such job"
            if job.status not in ACTIVE:
                return f"job is {job.status}"
            job.stop_requested = True
            if job.status == "queued":
                job.status = "stopped"
                return None
            process = job.process
        if process is not None:
            kill_tree(process)
        return None

    def resume(self, job_id: str) -> Optional[str]:
        """Put a stopped or failed job back in the queue with the same arguments. Return an error message, or None."""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return "no such job"
            if job.status not in ENDED:
                return f"job is {job.status}"
            job.status = "queued"
            job.stop_requested = False
            job.started = job.finished = None
            job.files = []
            job.log += "\n--- continued ---\n"
        self.pending.put(job)
        return None

    def delete(self, job_id: str) -> Optional[str]:
        """Delete the temp files of a stopped or failed job, and remove the job from the list.

        Return an error message, or None. Refuse when a queued or running job has the same URL
        or temp name, because that job uses the same files.
        """
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return "no such job"
            if job.status not in ENDED:
                return f"job is {job.status}"
            for other in self.jobs.values():
                if other.status in ACTIVE and (
                    other.url == job.url or (job.temp_name and other.temp_name == job.temp_name)
                ):
                    return "another job for the same input is queued or running"
            for path in temp_files.work_files(self.temp_dir, job.temp_name):
                path.unlink(missing_ok=True)
            try:
                self.temp_dir.rmdir()
            except OSError:
                pass
            del self.jobs[job_id]
            self.order.remove(job_id)
        return None

    def _work(self) -> None:
        while True:
            job = self.pending.get()
            with self.lock:
                if job.status != "queued":
                    continue
                job.started = time.monotonic()
                job.status = "running"
                job.process = subprocess.Popen(
                    job.args, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, **new_group_options(),
                )
            process = job.process
            for chunk in iter(lambda: process.stdout.read(256), ""):
                job.log = (job.log + chunk)[-MAX_LOG_CHARS:]
                job.temp_name = temp_files.name_from_log(job.log[-LOG_SCAN_CHARS:]) or job.temp_name
            code = process.wait()
            with self.lock:
                job.process = None
                job.files = self._files_from_log(job.log)
                job.finished = time.monotonic()
                if job.stop_requested and not (code == 0 and job.files):
                    job.status = "stopped"
                    job.log += "\n--- stopped ---\n"
                else:
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
            return [self._public(self.jobs[i]) for i in reversed(self.order)]

    def _public(self, job: Job) -> dict:
        """Return the job as JSON-ready data.

        `elapsed` is seconds since it started running. `temp_files` lists the names of its work files
        in temp/, only for a stopped or failed job.
        """
        data = {k: v for k, v in job.__dict__.items()
                if k not in ("args", "started", "finished", "process", "stop_requested")}
        data["elapsed"] = None if job.started is None else (job.finished or time.monotonic()) - job.started
        data["temp_files"] = (
            [p.name for p in temp_files.work_files(self.temp_dir, job.temp_name)] if job.status in ENDED else []
        )
        return data

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)


def new_group_options() -> dict:
    """Return Popen options that start the child in a new process group."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(process: subprocess.Popen) -> None:
    """Kill the process and all its child processes (yt-dlp, ffmpeg, deno)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
