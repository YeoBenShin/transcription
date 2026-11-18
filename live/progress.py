"""The transcript progress file: <session>/transcript.partial.jsonl.

Line 1 is {"header": {session, backend, model}}. Each next line is {start, end, text} in
timeline time. A line with text "" marks audio up to `end` as done with no speech.
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from app.models import Line
from app.stages.common.transcribe import complete_length, is_segment


class BadProgress(Exception):
    pass


class Progress:
    def __init__(self, path: Path, header: dict):
        """Open the file for append. Make it with `header` if it does not exist.

        A last line with no newline (a write cut short by a crash) is removed.
        Raise BadProgress if a complete line is not valid.
        """
        self.path = path
        self.records: List[dict] = []
        self.header = header
        if path.is_file():
            with path.open("r+b") as handle:
                data = handle.read()
                handle.truncate(complete_length(data))
            self.records, self.header = read(path)
        else:
            with path.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps({"header": header}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        self.handle = path.open("a", encoding="utf-8")

    @property
    def end(self) -> float:
        """Timeline time up to which all audio is done."""
        return self.records[-1]["end"] if self.records else 0.0

    def lines(self) -> List[Line]:
        return [Line(r["start"], r["text"]) for r in self.records if r["text"]]

    def prompt(self, count: int, since: float = 0.0) -> Optional[str]:
        """The text of the last `count` lines that start at or after `since`, or None."""
        texts = [r["text"] for r in self.records if r["text"] and r["start"] >= since][-count:]
        return " ".join(texts) or None

    def append(self, records: List[dict]) -> None:
        for record in records:
            self.handle.write(json.dumps(record) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.records.extend(records)

    def close(self) -> None:
        self.handle.close()


def read(path: Path):
    """Return (records, header) from the complete lines of the file."""
    data = path.read_bytes()
    try:
        text = data[: complete_length(data)].decode("utf-8")
        rows = [json.loads(raw) for raw in text.splitlines() if raw.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BadProgress(f"{path} cannot be read: {error}")
    if not rows or not isinstance(rows[0], dict) or "header" not in rows[0]:
        raise BadProgress(f"{path} has no header line")
    if not all(is_segment(r) for r in rows[1:]):
        raise BadProgress(f"{path} has a line that is not a segment")
    return rows[1:], rows[0]["header"]


def read_header(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    return read(path)[1]
