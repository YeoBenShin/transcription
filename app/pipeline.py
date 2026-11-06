"""The single entry point: route the input to a source, then write the transcript."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.models import Options, Transcript
from app.sources import SOURCES
from app.sources.base import Source
from app.stages.common.retry import GaveUp
from app.stages.common.transcribe import PARTIAL_NAME
from app.stages.common.write import write_full


class UnsupportedInput(ValueError):
    pass


class NoTranscript(RuntimeError):
    pass


@dataclass
class Result:
    transcript: Transcript
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
    """Get a transcript for any supported input and write it as full.txt.

    After full.txt is written, remove the Whisper progress file and the downloaded audio.
    Raise GaveUp if the Whisper path failed on every attempt.
    """
    source = route(raw)
    print(f"input type: {source.name}")
    transcript = source.get_transcript(raw, options)
    if transcript is None or not transcript.lines:
        raise NoTranscript(f"no transcript could be produced for {raw!r}")

    paths = write_full(transcript.lines, transcript.folder_name, options.outdir)
    (options.outdir / transcript.folder_name / PARTIAL_NAME).unlink(missing_ok=True)
    if transcript.downloaded_audio is not None:
        transcript.downloaded_audio.unlink(missing_ok=True)
    return Result(transcript, paths)
