"""Command line interface: python -m app <input> [options]."""

import argparse
import sys
from pathlib import Path

from app.models import Options, timestamp
from app.pipeline import NoTranscript, UnsupportedInput, run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app",
        description="Any supported input -> chunked transcript .txt files.",
    )
    parser.add_argument("input", help="full YouTube video URL, Spotify episode URL, or path to an audio/video file")
    parser.add_argument("--chunk-minutes", type=float, default=4.0)
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    parser.add_argument("--model", default="small", help="faster-whisper model size")
    args = parser.parse_args(argv)

    options = Options(chunk_minutes=args.chunk_minutes, outdir=args.outdir, model=args.model)
    try:
        result = run(args.input, options)
    except (UnsupportedInput, NoTranscript) as error:
        print(error, file=sys.stderr)
        return 1

    lines = result.transcript.lines
    print(
        f"\n{len(lines)} lines ({result.transcript.origin}), ~{timestamp(lines[-1].start)}, "
        f"{result.chunk_count} chunks of {args.chunk_minutes:g} min:"
    )
    for path in result.paths:
        print(f"  {path}")
    return 0
