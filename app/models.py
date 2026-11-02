"""Data types shared by all sources and stages."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

TARGET_LANGUAGE = "en"


@dataclass
class Line:
    start: float  # seconds from the beginning of the recording
    text: str


@dataclass
class Transcript:
    source_id: str  # name of the output folder for this input
    lines: List[Line]
    origin: str  # how the lines were made, e.g. "youtube captions" or "faster-whisper"


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
