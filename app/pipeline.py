"""The single entry point: route the input to a source, then chunk and write."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.models import Options, Transcript
from app.sources import SOURCES
from app.sources.base import Source
from app.stages.chunk import chunk
from app.stages.write import write_chunks


class UnsupportedInput(ValueError):
    pass


class NoTranscript(RuntimeError):
    pass


@dataclass
class Result:
    transcript: Transcript
    chunk_count: int
    paths: List[Path]


def route(raw: str, sources: Optional[List[Source]] = None) -> Source:
    """Return the first source that matches the raw input."""
    candidates = sources if sources is not None else SOURCES
    for source in candidates:
        if source.matches(raw):
            return source
    names = ", ".join(s.name for s in candidates)
    raise UnsupportedInput(f"no source handles {raw!r} (supported: {names})")


def run(raw: str, options: Options) -> Result:
    """Get a transcript for any supported input and write it as chunked .txt files."""
    source = route(raw)
    print(f"input type: {source.name}")
    transcript = source.get_transcript(raw, options)
    if transcript is None or not transcript.lines:
        raise NoTranscript(f"no transcript could be produced for {raw!r}")

    chunks = chunk(transcript.lines, options.chunk_seconds)
    paths = write_chunks(chunks, transcript.source_id, options.chunk_seconds, options.outdir)
    return Result(transcript, len(chunks), paths)
