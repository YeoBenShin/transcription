"""Terminal output of the live processes: fixed lines and one draft line that is replaced.

All live processes write to the same terminal. On a terminal, each fixed line starts with
"carriage return + erase line", so it replaces a draft line that is on the screen. The draft
line has no newline, so the next fixed line or draft replaces it. When stdout is not a
terminal, fixed lines are plain and drafts are not shown.

The draft shows the agreed words in normal text and the words that can still change in dim text.
"""

import shutil
import sys
from dataclasses import dataclass
from typing import Optional

from app.models import timestamp

ERASE = "\r\033[K"
DIM = "\033[2m"
RESET = "\033[0m"


def is_terminal() -> bool:
    return sys.stdout.isatty()


def say(text: str) -> None:
    """Print one fixed line."""
    print(f"{ERASE if is_terminal() else ''}{text}", flush=True)


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


def styled(caption: Caption, width: int) -> str:
    """The draft line text with ANSI styles, cut to the terminal width by fit."""
    prefix = f"[{timestamp(caption.start)}] "
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
