"""The transcripts in the output folder: file transcripts (<out>/*.txt) and live sessions (<out>/live/<name>/)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

LIVE_DIRNAME = "live"
PREVIEW_CHARS = 240
TIMESTAMP = re.compile(r"^\[\d\d:\d\d:\d\d\] ")


@dataclass
class HistoryItem:
    kind: str  # "file" or "live"
    title: str
    transcript: Optional[Path]  # the .txt file, None while a live session has no line yet
    modified: float  # seconds since the epoch
    text: str  # transcript lines without timestamps, one per line
    session: Optional[Path] = None  # live session folder
    finalized: bool = True  # live: the merged .flac exists

    @property
    def preview(self) -> str:
        joined = " ".join(self.text.split("\n"))
        return joined if len(joined) <= PREVIEW_CHARS else joined[:PREVIEW_CHARS].rstrip() + "…"


def read_text(path: Path) -> str:
    """The transcript lines of a .txt file in the app format (header line, blank line, '[HH:MM:SS] text'),
    without the timestamps."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[2:]
    except OSError:
        return ""
    return "\n".join(TIMESTAMP.sub("", line) for line in lines if line.strip())


def file_item(path: Path) -> HistoryItem:
    return HistoryItem("file", path.stem, path, path.stat().st_mtime, read_text(path))


def live_item(folder: Path) -> HistoryItem:
    transcript = folder / f"{folder.name}.txt"
    has_text = transcript.is_file()
    modified = (transcript if has_text else folder).stat().st_mtime
    return HistoryItem(
        "live",
        folder.name,
        transcript if has_text else None,
        modified,
        read_text(transcript) if has_text else "",
        session=folder,
        finalized=(folder / f"{folder.name}.flac").is_file(),
    )


def scan(outdir: Path) -> List[HistoryItem]:
    """All transcripts in the output folder, newest first."""
    outdir = Path(outdir)
    if not outdir.is_dir():
        return []
    items = [file_item(p) for p in outdir.glob("*.txt") if p.is_file()]
    live = outdir / LIVE_DIRNAME
    if live.is_dir():
        items += [live_item(folder) for folder in live.iterdir() if folder.is_dir()]
    return sorted(items, key=lambda item: item.modified, reverse=True)


def search(items: List[HistoryItem], query: str) -> List[HistoryItem]:
    """Items whose title or text contains the query (case is ignored). An empty query gives all items."""
    query = query.strip().lower()
    if not query:
        return list(items)
    return [i for i in items if query in i.title.lower() or query in i.text.lower()]
