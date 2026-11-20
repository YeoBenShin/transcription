"""Terminal output of the live processes: fixed lines and one draft line that is replaced.

All live processes write to the same terminal. On a terminal, each fixed line starts with
"carriage return + erase line", so it replaces a draft line that is on the screen. The draft
line has no newline, so the next fixed line or draft replaces it. When stdout is not a
terminal, fixed lines are plain and drafts are not shown.

The draft shows the agreed words in normal text and the words that can still change in dim text.

With the environment variable LIVE_EVENTS=1 (set by the desktop app), each output is one JSON line
instead: {"event": "log" | "line" | "draft" | "session" | "state", ...}.
"""

import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Optional

from app.models import timestamp

ERASE = "\r\033[K"
DIM = "\033[2m"
RESET = "\033[0m"
EVENTS_ENV = "LIVE_EVENTS"


def is_terminal() -> bool:
    return sys.stdout.isatty()


def events_mode() -> bool:
    return os.environ.get(EVENTS_ENV) == "1"


def event(kind: str, **fields) -> None:
    """Print one JSON event line in events mode. Does nothing otherwise."""
    if events_mode():
        print(json.dumps({"event": kind, **fields}), flush=True)


def say(text: str) -> None:
    """Print one fixed line."""
    if events_mode():
        event("log", text=text)
        return
    print(f"{ERASE if is_terminal() else ''}{text}", flush=True)


def line(start: float, text: str) -> None:
    """Print one transcript line that will not change."""
    if events_mode():
        event("line", start=start, text=text)
        return
    say(f"[{timestamp(start)}] {text}")


def fit(prefix: str, text: str, width: int) -> str:
    """prefix + text in at most width - 1 characters. A long text keeps its end, after '…'."""
    room = width - 1 - len(prefix)
    if len(text) > room:
        text = "…" + text[-(room - 1):] if room > 1 else ""
    return prefix + text


@dataclass
class Caption:
    start: float  # timeline time of the first word
    agreed: str  # words that do not change
    tail: str  # words after them that can change
    label: str = ""  # stream label, shown before the words


def styled(caption: Caption, width: int) -> str:
    """The draft line text with ANSI styles, cut to the terminal width by fit."""
    prefix = f"[{timestamp(caption.start)}] " + (f"{caption.label}: " if caption.label else "")
    full = " ".join(t for t in (caption.agreed, caption.tail) if t)
    shown = fit(prefix, full, width)[len(prefix):]
    cut = max(len(shown) - len(caption.tail), 0)
    return f"{prefix}{shown[:cut]}{DIM}{shown[cut:]}{RESET}"


class Draft:
    """The one draft line of this process."""

    def __init__(self):
        self.shown = False

    def show(self, caption: Optional[Caption]) -> None:
        """Replace the draft line with `caption`. None removes it."""
        if events_mode():
            if caption is None:
                event("draft", start=None, agreed="", tail="", label="")
            else:
                event("draft", start=caption.start, agreed=caption.agreed, tail=caption.tail, label=caption.label)
            return
        if not is_terminal():
            return
        if caption is None:
            self.clear()
            return
        sys.stdout.write(f"{ERASE}{styled(caption, shutil.get_terminal_size().columns)}")
        sys.stdout.flush()
        self.shown = True

    def clear(self) -> None:
        if self.shown:
            sys.stdout.write(ERASE)
            sys.stdout.flush()
            self.shown = False
