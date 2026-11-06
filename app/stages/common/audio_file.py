"""Find a completed audio download in an output folder."""

from pathlib import Path
from typing import Optional

AUDIO_STEM = "audio"
INCOMPLETE_SUFFIXES = {".part", ".ytdl", ".tmp"}


def find_audio(directory: Path) -> Optional[Path]:
    """Return directory/audio.<ext> if a completed download is there, else None.

    Files with a suffix in INCOMPLETE_SUFFIXES are not complete and are ignored.
    """
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob(f"{AUDIO_STEM}.*")):
        if path.is_file() and path.suffix not in INCOMPLETE_SUFFIXES and path.stat().st_size > 0:
            return path
    return None
