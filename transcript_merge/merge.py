"""Read transcripts in the `app` format and merge them into one list of lines in time order."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.models import Line

_LINE = re.compile(r"^\[(\d+):(\d\d):(\d\d)\] ?(.*)$")


class BadTranscript(Exception):
    pass


@dataclass
class Source:
    """One input transcript and the label put before each of its lines ("" for no label)."""

    path: Path
    label: str = ""


def parse_seconds(hours: str, minutes: str, seconds: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def read_lines(path: Path) -> List[Line]:
    """The lines of a transcript file in the `app` format.

    Format: an optional header line (`<name>-<source id>`), a blank line, then one
    `[HH:MM:SS] text` line per segment. A non-blank line with no timestamp continues the line
    before it. Raise BadTranscript if a text line comes before the first timestamp line.
    """
    lines: List[Line] = []
    rows = path.read_text(encoding="utf-8").splitlines()
    first = 1
    if rows and not _LINE.match(rows[0]):
        rows, first = rows[1:], 2
    for number, row in enumerate(rows, start=first):
        if not row.strip():
            continue
        match = _LINE.match(row)
        if match:
            lines.append(Line(parse_seconds(*match.groups()[:3]), match.group(4).strip()))
        elif lines:
            lines[-1] = Line(lines[-1].start, f"{lines[-1].text} {row.strip()}")
        else:
            raise BadTranscript(f"{path}:{number}: text before the first [HH:MM:SS] line")
    return lines


def merge(sources: List[Source]) -> List[Line]:
    """The lines of all sources in time order, each with its label before the text.

    Lines with the same time keep the order of the sources, then their order in the file.
    """
    labelled = []
    for index, source in enumerate(sources):
        for order, line in enumerate(read_lines(source.path)):
            text = f"{source.label}: {line.text}" if source.label else line.text
            labelled.append((line.start, index, order, Line(line.start, text)))
    return [line for *_, line in sorted(labelled, key=lambda item: item[:3])]


def default_name(sources: List[Source]) -> Optional[str]:
    """The longest common start of the file names without '.txt', with '.', '-', '_' and spaces
    stripped from its end. None if it is empty.

    'X.system.txt' and 'X.mic.txt' give 'X'.
    """
    stems = [source.path.stem for source in sources]
    common = stems[0]
    for stem in stems[1:]:
        while not stem.startswith(common):
            common = common[:-1]
    return common.rstrip(".-_ ") or None
