"""Find a completed audio download in the temp folder."""

from pathlib import Path
from typing import Optional

INCOMPLETE_SUFFIXES = {".part", ".ytdl", ".tmp"}


def find_audio(directory: Path, stem: str) -> Optional[Path]:
    """Return directory/<stem>.<ext> if a completed download is there, else None.

    <ext> is one suffix with no dot. Thus <stem>.partial.jsonl and <stem>.<ext>.part do not match.
    Files with a suffix in INCOMPLETE_SUFFIXES are not complete and are ignored.
    """
    if not directory.is_dir():
        return None
    prefix = stem + "."
    for path in sorted(directory.iterdir()):
        ext = path.name[len(prefix):] if path.name.startswith(prefix) else ""
        if (
            ext
            and "." not in ext
            and path.suffix not in INCOMPLETE_SUFFIXES
            and path.is_file()
            and path.stat().st_size > 0
        ):
            return path
    return None
