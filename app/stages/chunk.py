"""Stage: split lines into fixed-length time windows."""

from typing import List

from app.models import Line


def chunk(lines: List[Line], chunk_seconds: float) -> List[List[Line]]:
    """Bucket lines by the chunk_seconds window that their start falls in. Drop empty windows."""
    if not lines:
        return []
    chunks: List[List[Line]] = []
    for line in lines:
        index = int(line.start // chunk_seconds)
        while len(chunks) <= index:
            chunks.append([])
        chunks[index].append(line)
    return [c for c in chunks if c]
