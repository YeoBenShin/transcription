"""A child process of the app: output line by line to a callback, stop by closing stdin, kill with all its children."""

import os
import re
import signal
import subprocess
import sys
import threading
from typing import Callable, Dict, List, Optional

from live.session import pid_alive

LINE_BREAK = re.compile(rb"\r\n|\r|\n")
READ_BYTES = 4096


def group_flags() -> dict:
    """Start the child in its own process group, with no console window on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def is_running(pid: int) -> bool:
    return pid_alive(pid)


class Child:
    """Runs argv with stdout and stderr merged. Each output line (split at \\n or \\r, empty lines left out)
    goes to on_line from a reader thread. on_exit gets the exit code after the last line."""

    def __init__(self, argv: List[str], on_line: Callable[[str], None],
                 on_exit: Optional[Callable[[int], None]] = None, env: Optional[Dict[str, str]] = None,
                 cwd: Optional[str] = None):
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env, cwd=cwd, bufsize=0, **group_flags())
        self.on_line = on_line
        self.on_exit = on_exit
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def _read(self) -> None:
        rest = b""
        while True:
            data = self.process.stdout.read(READ_BYTES)  # unbuffered: returns what is there, b"" at the end
            if not data:
                break
            parts = LINE_BREAK.split(rest + data)
            rest = parts.pop()
            for part in parts:
                self._emit(part)
        self._emit(rest)
        code = self.process.wait()
        if self.on_exit:
            self.on_exit(code)

    def _emit(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            self.on_line(text)

    def close_stdin(self) -> None:
        """Tell the child to finish: it reads end of file on stdin."""
        try:
            self.process.stdin.close()
        except OSError:
            pass

    def kill(self) -> None:
        """Kill the child and all its child processes."""
        if self.process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def wait(self, timeout: Optional[float] = None) -> int:
        code = self.process.wait(timeout)
        self.reader.join(timeout)
        return code

    def running(self) -> bool:
        return self.process.poll() is None
