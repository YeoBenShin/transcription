"""Data types shared by all sources and stages."""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

TARGET_LANGUAGE = "en"
MAX_TITLE = 120
MAX_AUTHOR = 60
UNKNOWN_DATE = "00000000"
UNKNOWN_AUTHOR = "Unknown"
TEMP_DIRNAME = "temp"
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
    name: str  # output file stem, from output_name()
    downloaded_audio: Optional[Path] = None  # audio file the source downloaded; removed after the .txt is written


@dataclass
class Options:
    chunk_minutes: float = 4.0
    outdir: Path = field(default_factory=lambda: Path("out"))
    model: str = "base"

    @property
    def chunk_seconds(self) -> float:
        return self.chunk_minutes * 60

    @property
    def temp_dir(self) -> Path:
        """Folder for Whisper progress files and downloaded audio."""
        return self.outdir / TEMP_DIRNAME


def output_name(
    published: Optional[date], author: Optional[str], title: Optional[str], source_id: str
) -> str:
    """'YYYYMMDD-Author-Title'.

    No date gives UNKNOWN_DATE, no usable author gives UNKNOWN_AUTHOR, no usable title gives source_id.
    """
    day = published.strftime("%Y%m%d") if published else UNKNOWN_DATE
    who = safe_name(author or "", MAX_AUTHOR) or UNKNOWN_AUTHOR
    what = safe_name(title or "", MAX_TITLE) or safe_name(source_id, MAX_TITLE)
    return f"{day}-{who}-{what}"


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def clean(text: str) -> str:
    return " ".join(text.split())


def safe_name(text: str, limit: int = MAX_TITLE) -> str:
    """Remove characters that are not allowed in file names on macOS, Linux or Windows.

    Collapse whitespace, cut to `limit` characters, strip leading and trailing dots and spaces.
    """
    name = clean(_UNSAFE.sub(" ", text))[:limit]
    return name.strip(" .")
