"""Command line interface: python -m app <input> [options]."""

import argparse
import sys
from pathlib import Path

from app.models import Options, timestamp
from app.pipeline import GaveUp, NoTranscript, UnsupportedInput, run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Any supported input -> transcript .txt file.",
    )
    parser.add_argument("input", help="full YouTube video URL, Spotify episode URL, or path to an audio/video file")
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    parser.add_argument("--model", default="small", help="faster-whisper model size")
    args = parser.parse_args(argv)

    options = Options(outdir=args.outdir, model=args.model)
    try:
        result = run(args.input, options)
    except (UnsupportedInput, NoTranscript, GaveUp) as error:
        print(error, file=sys.stderr)
        return 1

    lines = result.transcript.lines
    print(
        f"\n{len(lines)} lines ({result.transcript.origin}), ~{timestamp(lines[-1].start)}:"
    )
    for path in result.paths:
        print(f"  {path}")
    return 0
