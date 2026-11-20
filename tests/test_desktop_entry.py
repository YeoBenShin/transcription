"""The one entry point of the packaged app: no arguments opens the window, `-m module args` runs a module
like `python -m`, so child processes work the same from source and from the packaged app."""

import subprocess
import sys

from desktop import entry


def test_module_arguments_run_the_module(monkeypatch):
    calls = []
    monkeypatch.setattr(entry.runpy, "run_module", lambda name, run_name, alter_sys: calls.append((name, list(sys.argv))))
    monkeypatch.setattr(entry, "open_window", lambda: calls.append("window"))
    entry.main(["app-binary", "-m", "live.recorder", "folder", "--stream", "mic"])
    assert calls == [("live.recorder", ["live.recorder", "folder", "--stream", "mic"])]


def test_unbuffered_flag_is_accepted_before_m(monkeypatch):
    calls = []
    monkeypatch.setattr(entry.runpy, "run_module", lambda name, run_name, alter_sys: calls.append(name))
    entry.main(["app-binary", "-u", "-m", "app", "x"])
    assert calls == ["app"]


def test_no_arguments_opens_the_window(monkeypatch):
    calls = []
    monkeypatch.setattr(entry, "open_window", lambda: calls.append("window") or 0)
    assert entry.main(["app-binary"]) == 0
    assert calls == ["window"]


def test_exit_code_of_module_is_kept():
    result = subprocess.run([sys.executable, "-m", "desktop.entry", "-m", "app", "--bad-option"], capture_output=True)
    assert result.returncode == 2
    assert b"usage: python -m app" in result.stderr
