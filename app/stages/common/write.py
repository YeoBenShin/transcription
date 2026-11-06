"""Stage: write transcript .txt files."""

from pathlib import Path
from typing import List

from app.models import Line, timestamp


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


def write_full(lines: List[Line], name: str, outdir: Path) -> List[Path]:
    """Write full.txt into outdir/name. Write a temp file first, then rename it to full.txt."""
    directory = outdir / name
    directory.mkdir(parents=True, exist_ok=True)
    full = directory / "full.txt"
    tmp = full.with_name(full.name + ".tmp")
    tmp.write_text("\n".join(f"[{timestamp(l.start)}] {l.text}" for l in lines) + "\n")
    tmp.replace(full)
    return [full]
