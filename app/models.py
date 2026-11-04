"""Data types shared by all sources and stages."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

TARGET_LANGUAGE = "en"
MAX_FOLDER_NAME = 120
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Line:
    start: float  # seconds from the beginning of the recording
    text: str


@dataclass
class Transcript:
    source_id: str  # platform id, or file name without extension
    lines: List[Line]
    origin: str  # how the lines were made, e.g. "youtube captions" or "faster-whisper"
    title: Optional[str] = None  # video or episode name, if known

    @property
    def folder_name(self) -> str:
        """'<safe title> [<source_id>]'. Only the source_id if there is no usable title."""
        name = safe_name(self.title or "")
        return f"{name} [{self.source_id}]" if name else self.source_id


@dataclass
class Options:
    chunk_minutes: float = 4.0
    outdir: Path = field(default_factory=lambda: Path("out"))
    model: str = "small"

    @property
    def chunk_seconds(self) -> float:
        return self.chunk_minutes * 60


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def clean(text: str) -> str:
    return " ".join(text.split())


def safe_name(text: str) -> str:
    """Remove characters that are not allowed in file names on macOS, Linux or Windows.

    Collapse whitespace, cut to MAX_FOLDER_NAME characters, strip leading and trailing dots and spaces.
    """
    name = clean(_UNSAFE.sub(" ", text))[:MAX_FOLDER_NAME]
    return name.strip(" .")
