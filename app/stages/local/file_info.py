"""Stage: get the recording date and author of a local audio or video file."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

DATE_TAGS = ("creation_time", "date")
AUTHOR_TAGS = ("artist", "album_artist", "author", "composer")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class FileInfo:
    recorded: date
    author: Optional[str]


def read_tags(path: Path) -> dict:
    """Return the container tags with lower-case keys, or {} if the file cannot be opened."""
    import av

    try:
        with av.open(str(path)) as container:
            return {k.lower(): v for k, v in container.metadata.items()}
    except (av.FFmpegError, OSError):
        return {}


def tag_date(tags: dict) -> Optional[date]:
    """Return the first DATE_TAGS value that starts with YYYY-MM-DD, as a date."""
    for key in DATE_TAGS:
        match = _ISO_DATE.match(str(tags.get(key, "")).strip())
        if match:
            try:
                return date(*map(int, match.groups()))
            except ValueError:
                continue
    return None


def file_date(path: Path) -> date:
    """Return the file creation date if the OS has it, else the modification date, in local time."""
    stat = path.stat()
    seconds = getattr(stat, "st_birthtime", None) or stat.st_mtime
    return datetime.fromtimestamp(seconds).date()


def fetch_file_info(path: Path) -> FileInfo:
    """Return the date from the file tags, else the file date, and the first non-empty AUTHOR_TAGS value."""
    tags = read_tags(path)
    author = next((str(tags[k]).strip() for k in AUTHOR_TAGS if str(tags.get(k, "")).strip()), None)
    info = FileInfo(tag_date(tags) or file_date(path), author)
    print(f"local file: {info.recorded} {info.author!r}")
    return info
