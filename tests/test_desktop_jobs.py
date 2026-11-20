"""Job queue and live recording objects, with small Python scripts as the child processes."""

import json
import sys

import pytest

pytest.importorskip("PySide6")

from desktop import jobs  # noqa: E402


def script(code: str) -> list:
    return [sys.executable, "-c", code]


def wait_status(qtbot, job, status, timeout=10000):
    qtbot.waitUntil(lambda: job.status == status, timeout=timeout)


def test_jobs_run_one_at_a_time_in_order(qtbot, tmp_path):
    queue = jobs.JobQueue()
    marker = tmp_path / "order.txt"
    first = queue.submit("file", "a", script(f"import time; time.sleep(0.5); open(r'{marker}', 'a').write('a')"))
    second = queue.submit("file", "b", script(f"open(r'{marker}', 'a').write('b')"))
    assert first.status == jobs.RUNNING and second.status == jobs.QUEUED
    wait_status(qtbot, second, jobs.DONE)
    assert marker.read_text() == "ab"


def test_progress_lines_set_the_detail(qtbot):
    queue = jobs.JobQueue()
    job = queue.submit("file", "a", script("import time; print('  [00:01:05] hello', end='\\r', flush=True); time.sleep(1)"))
    qtbot.waitUntil(lambda: job.detail == "Transcribing… 00:01:05", timeout=10000)
    wait_status(qtbot, job, jobs.DONE)


def test_batch_lines_set_the_detail(qtbot):
    queue = jobs.JobQueue()
    job = queue.submit("folder", "inbox", script("import time; print('[2/5] talk.m4a', flush=True); time.sleep(1)"))
    qtbot.waitUntil(lambda: job.detail.startswith("File 2 of 5: talk.m4a"), timeout=10000)


def test_failed_job_shows_last_line(qtbot):
    queue = jobs.JobQueue()
    job = queue.submit("url", "x", script("import sys; print('no transcript for this input'); sys.exit(1)"))
    wait_status(qtbot, job, jobs.FAILED)
    assert job.detail == "Failed: no transcript for this input"


def test_stop_kills_running_and_cancels_queued(qtbot):
    queue = jobs.JobQueue()
    running = queue.submit("file", "a", script("import time; time.sleep(60)"))
    queued = queue.submit("file", "b", script("pass"))
    queued.stop()
    assert queued.status == jobs.STOPPED
    running.stop()
    wait_status(qtbot, running, jobs.STOPPED)


EVENTS = [
    {"event": "session", "folder": "/out/live/s1"},
    {"event": "state", "state": "recording"},
    {"event": "draft", "start": 1.0, "agreed": "hello", "tail": "wor", "label": ""},
    {"event": "line", "start": 1.0, "text": "hello world."},
    {"event": "log", "text": "rec: recording"},
]


def test_live_recording_turns_events_into_signals(qtbot):
    code = "import sys\n" + "".join(f"print({json.dumps(json.dumps(e))}, flush=True)\n" for e in EVENTS) + \
        "print('plain text', flush=True)\nsys.stdin.read()\nprint('{\"event\": \"state\", \"state\": \"done\"}')\n"
    live = jobs.LiveRecording(script(code))
    seen = []
    live.session_started.connect(lambda f: seen.append(("session", f)))
    live.state_changed.connect(lambda s: seen.append(("state", s)))
    live.line_added.connect(lambda start, text: seen.append(("line", start, text)))
    live.draft_changed.connect(lambda d: seen.append(("draft", d["agreed"], d["tail"])))
    live.log.connect(lambda t: seen.append(("log", t)))
    live.start()
    qtbot.waitUntil(lambda: ("log", "plain text") in seen, timeout=10000)
    with qtbot.waitSignal(live.finished, timeout=10000):
        live.stop()
    assert seen == [
        ("session", "/out/live/s1"), ("state", "recording"), ("draft", "hello", "wor"),
        ("line", 1.0, "hello world."), ("log", "rec: recording"), ("log", "plain text"), ("state", "done"),
    ]
