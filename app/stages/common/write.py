"""Stage: write transcript .txt files."""

from pathlib import Path
from typing import List, Optional

from app.models import Line, safe_name, timestamp


def write_chunks(
    chunks: List[List[Line]], name: str, chunk_seconds: float, outdir: Path
) -> List[Path]:
    """Write one file per chunk plus full.txt into outdir/name. Remove old chunk files first."""
    directory = outdir / name
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("chunk_*.txt"):
        stale.unlink()

    paths = []
    for number, lines in enumerate(chunks, start=1):
        index = int(lines[0].start // chunk_seconds)
        start, end = index * chunk_seconds, (index + 1) * chunk_seconds
        path = directory / f"chunk_{number:03d}_{timestamp(start)}-{timestamp(end)}.txt".replace(":", "")
        header = (
            f"# {name} chunk {number}/{len(chunks)} "
            f"[{timestamp(start)} - {timestamp(end)}]\n\n"
        )
        body = "\n".join(f"[{timestamp(l.start)}] {l.text}" for l in lines)
        path.write_text(header + body + "\n")
        paths.append(path)

    full = directory / "full.txt"
    full.write_text(
        "\n".join(f"[{timestamp(l.start)}] {l.text}" for c in chunks for l in c) + "\n"
    )
    paths.append(full)
    return paths


def first_line(path: Path) -> Optional[str]:
    """Return the first line of path without its newline, or None if the file is missing."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return handle.readline().rstrip("\n")
    except FileNotFoundError:
        return None


def full_path(name: str, source_id: str, outdir: Path) -> Path:
    """Return the transcript path for this input.

    outdir/<name>.txt if it does not exist or its header is <name>-<source_id>.
    Else outdir/<name>-<source_id>.txt.
    """
    full = outdir / f"{name}.txt"
    header = first_line(full)
    if header is None or header == f"{name}-{source_id}":
        return full
    return outdir / f"{name}-{safe_name(source_id)}.txt"


def write_full(lines: List[Line], name: str, source_id: str, outdir: Path) -> List[Path]:
    """Write the transcript with the header line '<name>-<source_id>', then a blank line.

    The path comes from full_path. Write a .tmp file first, then rename it.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    full = full_path(name, source_id, outdir)
    body = "\n".join(f"[{timestamp(l.start)}] {l.text}" for l in lines)
    tmp = full.with_name(full.name + ".tmp")
    tmp.write_text(f"{name}-{source_id}\n\n{body}\n", encoding="utf-8")
    tmp.replace(full)
    return [full]
