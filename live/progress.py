"""The transcript progress file of one stream: <session>/transcript.<stream>.partial.jsonl.

Line 1 is {"header": {session, backend, model}}. Each next line is {start, end, text} in
timeline time, with "open": true if the transcript line goes on in the next record. A transcript
line is one record, or a run of open records and the record after them. A record with text ""
marks audio up to `end` as done with no speech, and closes an open line.
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

from app.models import Line
from app.stages.common.transcribe import complete_length, is_segment
from live.loops import has_loop

PROMPT_CHARS = 200


@dataclass
class TextLine:
    start: float
    end: float
    text: str
    closed: bool


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
        """Timeline time up to which all audio is done: the end of the last record that is not open."""
        return next((r["end"] for r in reversed(self.records) if not r.get("open")), 0.0)

    def text_lines(self) -> List[TextLine]:
        """Transcript lines: open records joined with the records after them. The last line can be open."""
        lines: List[TextLine] = []
        current: Optional[TextLine] = None
        for record in self.records:
            if record["text"]:
                if current is None:
                    current = TextLine(record["start"], record["end"], record["text"], False)
                else:
                    current.end, current.text = record["end"], f"{current.text} {record['text']}"
            if not record.get("open") and current is not None:
                current.closed = True
                lines.append(current)
                current = None
        if current is not None:
            lines.append(current)
        return lines

    def lines(self) -> List[Line]:
        return [Line(line.start, line.text) for line in self.text_lines()]

    def closed_count(self) -> int:
        return sum(line.closed for line in self.text_lines())

    def open_records(self) -> List[dict]:
        """The records after the last record that is not open."""
        count = 0
        while count < len(self.records) and self.records[-1 - count].get("open"):
            count += 1
        return self.records[len(self.records) - count:]

    def prompt(self, since: float, before: float, chars: int = PROMPT_CHARS) -> Optional[str]:
        """The last `chars` characters of the text of the lines in [since, before), or None.

        Lines with a loop are left out. A word cut by the character limit is left out.
        """
        texts = [
            line.text
            for line in self.text_lines()
            if line.start >= since and line.end <= before and not has_loop(line.text)
        ]
        text = " ".join(texts)
        if len(text) > chars:
            cut = text[-chars:]
            if text[-chars - 1] != " ":
                cut = cut.split(" ", 1)[1] if " " in cut else ""
            text = cut
        return text or None

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
