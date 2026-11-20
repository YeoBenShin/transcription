"""Child processes as Qt objects: the job queue (file, folder, URL, merge, finalize; one at a time) and
the live recording (JSON events from `live start`).

The child output comes from a reader thread. Each object re-emits it through its own signals, which
Qt delivers in the thread of the object (the GUI thread).
"""

import json
import re
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from desktop import commands, process

PROGRESS_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] ")
BATCH_LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+)$")
MAX_LOG_LINES = 2000

QUEUED, RUNNING, DONE, FAILED, STOPPED = "queued", "running", "done", "failed", "stopped"


class Job(QObject):
    """One child process of the queue. `detail` is a short status for the card."""

    changed = Signal()
    _line = Signal(str)
    _exit = Signal(int)

    def __init__(self, kind: str, title: str, argv: List[str], env: Optional[Dict[str, str]] = None):
        super().__init__()
        self.kind = kind  # file | folder | url | merge | finalize
        self.title = title
        self.argv = argv
        self.env = env
        self.status = QUEUED
        self.detail = "In queue…"
        self.log: List[str] = []
        self.batch = ""  # "File n of N: name" while a folder job runs
        self.child: Optional[process.Child] = None
        self.stop_requested = False
        self._line.connect(self._on_line)
        self._exit.connect(self._on_exit)

    def start(self) -> None:
        self.status = RUNNING
        self.detail = "Starting…"
        self.changed.emit()
        try:
            self.child = process.Child(self.argv, on_line=self._line.emit, on_exit=self._exit.emit, env=self.env)
        except OSError as error:
            self._on_line(f"cannot start: {error}")
            self._on_exit(-1)

    def stop(self) -> None:
        self.stop_requested = True
        if self.status == QUEUED:
            self.status = STOPPED
            self.detail = "Stopped"
            self.changed.emit()
        elif self.child is not None:
            self.child.kill()

    def _on_line(self, line: str) -> None:
        self.log = (self.log + [line])[-MAX_LOG_LINES:]
        batch = BATCH_LINE.match(line)
        progress = PROGRESS_LINE.match(line)
        if batch:
            self.batch = f"File {batch.group(1)} of {batch.group(2)}: {batch.group(3)}"
            self.detail = self.batch
        elif progress:
            self.detail = f"{self.batch} · {progress.group(1)}" if self.batch else f"Transcribing… {progress.group(1)}"
        self.changed.emit()

    def _on_exit(self, code: int) -> None:
        if self.stop_requested:
            self.status, self.detail = STOPPED, "Stopped"
        elif code == 0:
            self.status, self.detail = DONE, "Done"
        else:
            self.status = FAILED
            self.detail = "Failed: " + (self.log[-1] if self.log else f"exit {code}")
        self.changed.emit()


class JobQueue(QObject):
    """Runs jobs one at a time, in the order they were added."""

    job_added = Signal(object)
    job_finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.jobs: List[Job] = []

    def submit(self, kind: str, title: str, argv: List[str]) -> Job:
        job = Job(kind, title, argv, env=commands.child_env())
        job.changed.connect(lambda job=job: self._on_change(job))
        self.jobs.append(job)
        self.job_added.emit(job)
        self._next()
        return job

    def remove(self, job: Job) -> None:
        job.stop()
        if job in self.jobs:
            self.jobs.remove(job)

    def active(self) -> List[Job]:
        return [j for j in self.jobs if j.status in (QUEUED, RUNNING)]

    def _on_change(self, job: Job) -> None:
        if job.status in (DONE, FAILED, STOPPED):
            self.job_finished.emit(job)
            self._next()

    def _next(self) -> None:
        if any(j.status == RUNNING for j in self.jobs):
            return
        queued = next((j for j in self.jobs if j.status == QUEUED), None)
        if queued is not None:
            queued.start()

    def stop_all(self) -> None:
        for job in self.active():
            job.stop()


class LiveRecording(QObject):
    """One `live start` child. Its JSON event lines become signals; other lines become log lines."""

    session_started = Signal(str)  # session folder
    state_changed = Signal(str)  # recording | stopping | finishing | done | failed
    line_added = Signal(float, str)
    draft_changed = Signal(object)  # dict with start, agreed, tail, label; start None when there is no draft
    log = Signal(str)
    finished = Signal(int)
    _line = Signal(str)
    _exit = Signal(int)

    def __init__(self, argv: List[str]):
        super().__init__()
        self.argv = argv
        self.child: Optional[process.Child] = None
        self.folder = ""
        self.state = ""
        self.logs: List[str] = []
        self._line.connect(self._on_line)
        self._exit.connect(self._on_exit)

    def start(self) -> None:
        self.child = process.Child(self.argv, on_line=self._line.emit, on_exit=self._exit.emit,
                                   env=commands.child_env())

    def stop(self) -> None:
        """Stop the recording. The child then transcribes the audio that is left and finalizes the session."""
        if self.child is not None:
            self.child.close_stdin()

    def kill(self) -> None:
        if self.child is not None:
            self.child.kill()

    def running(self) -> bool:
        return self.child is not None and self.child.running()

    def _on_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if not isinstance(event, dict) or "event" not in event:
            self._log(line)
            return
        kind = event["event"]
        if kind == "session":
            self.folder = event.get("folder", "")
            self.session_started.emit(self.folder)
        elif kind == "state":
            self.state = event.get("state", "")
            self.state_changed.emit(self.state)
        elif kind == "line":
            self.line_added.emit(float(event.get("start", 0.0)), event.get("text", ""))
        elif kind == "draft":
            self.draft_changed.emit(event)
        elif kind == "log":
            self._log(event.get("text", ""))

    def _log(self, text: str) -> None:
        self.logs = (self.logs + [text])[-MAX_LOG_LINES:]
        self.log.emit(text)

    def _on_exit(self, code: int) -> None:
        self.finished.emit(code)
