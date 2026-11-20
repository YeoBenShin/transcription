"""Command line interface: python -m transcript_merge [LABEL=]FILE [LABEL=]FILE ... [--name NAME] [--outdir DIR]."""

import argparse
import sys
from pathlib import Path
from typing import List

from app.stages.common.write import write_full
from transcript_merge.merge import BadTranscript, Source, default_name, merge

SOURCE_ID = "merged"
DEFAULT_NAME = "merged"


def parse_source(raw: str) -> Source:
    """'LABEL=PATH' gives a labelled source, 'PATH' one with no label. An existing file is always a PATH."""
    path = Path(raw).expanduser()
    if path.is_file() or "=" not in raw:
        return Source(path)
    label, _, rest = raw.partition("=")
    return Source(Path(rest).expanduser(), label.strip())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m transcript_merge",
        description="Transcripts in the app format, on the same time axis -> one transcript in time order.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        metavar="[LABEL=]FILE",
        help="transcript .txt file. With LABEL=, each of its lines starts with 'LABEL: '. Example: Them=a.txt Me=b.txt",
    )
    parser.add_argument("--name", help="output file name without .txt (default: the common start of the input names)")
    parser.add_argument("--outdir", type=Path, help="output folder (default: the folder of the first input)")
    args = parser.parse_args(argv)

    sources: List[Source] = [parse_source(raw) for raw in args.sources]
    missing = [str(s.path) for s in sources if not s.path.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        lines = merge(sources)
    except BadTranscript as error:
        print(error, file=sys.stderr)
        return 1

    name = args.name or default_name(sources) or DEFAULT_NAME
    outdir = args.outdir or sources[0].path.parent
    [path] = write_full(lines, name, SOURCE_ID, outdir)
    print(f"{len(lines)} lines from {len(sources)} transcripts: {path}")
    return 0
