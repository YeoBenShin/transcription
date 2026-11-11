"""Name the work files of one transcript in the temp folder: announce the name, list the files."""

from pathlib import Path
from typing import List

from app.stages.common.audio_file import INCOMPLETE_SUFFIXES
from app.stages.common.transcribe import PARTIAL_SUFFIX

LOG_PREFIX = "temp name: "


def announce(name: str) -> None:
    """Print 'temp name: <name>'. The web runner reads this line to find the job's work files."""
    print(f"{LOG_PREFIX}{name}", flush=True)


def name_from_log(log: str) -> str:
    """Return <name> from the last complete 'temp name: <name>' line in log, or '' if there is none."""
    name = ""
    for line in log[: log.rfind("\n") + 1].splitlines():
        if line.startswith(LOG_PREFIX):
            name = line[len(LOG_PREFIX):].strip()
    return name


def work_files(directory: Path, stem: str) -> List[Path]:
    """Return the work files of `stem` in directory.

    These are <stem>.partial.jsonl, <stem>.<ext>, and <stem>.<ext><s> for s in INCOMPLETE_SUFFIXES.
    <ext> has no dot. Names are compared as text, not with glob, because titles can contain '[' and ']'.
    """
    if not stem or not directory.is_dir():
        return []
    prefix = stem + "."
    found = []
    for path in sorted(directory.iterdir()):
        if not path.name.startswith(prefix) or not path.is_file():
            continue
        rest = path.name[len(prefix):]
        if path.name == stem + PARTIAL_SUFFIX:
            found.append(path)
            continue
        base = rest[: -len(path.suffix)] if path.suffix in INCOMPLETE_SUFFIXES else rest
        if base and "." not in base:
            found.append(path)
    return found
