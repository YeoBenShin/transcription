"""Batch entry point: python -m app.batch [options]. Transcribe every media file in a folder."""

import argparse
import shutil
import sys
from pathlib import Path
from typing import List

from app.models import Options
from app.pipeline import run

DEFAULT_INBOX = Path("to-be-transcribe")
DONE_DIRNAME = "done"
MEDIA_SUFFIXES = {
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma", ".webm",
    ".mp4", ".mkv", ".mov", ".avi", ".m4v",
}


def find_media(inbox: Path) -> List[Path]:
    """Return the media files directly inside inbox, sorted by name. Hidden files are skipped."""
    return sorted(
        p for p in inbox.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in MEDIA_SUFFIXES
    )


def move_to_done(path: Path, done: Path) -> Path:
    """Move path into done. Add a numeric suffix if the name is already taken."""
    done.mkdir(exist_ok=True)
    target = done / path.name
    counter = 1
    while target.exists():
        target = done / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), target)
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.batch",
        description="Transcribe every audio/video file in a folder into transcript .txt files.",
    )
    parser.add_argument("--inbox", type=Path, default=DEFAULT_INBOX, help="folder with the files to transcribe")
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    parser.add_argument("--model", default="base", help="faster-whisper model size")
    parser.add_argument("--keep", action="store_true", help="do not move processed files into <inbox>/done")
    args = parser.parse_args(argv)

    inbox = args.inbox.expanduser()
    if not inbox.is_dir():
        inbox.mkdir(parents=True)
        print(f"created {inbox}/. Put audio/video files in it and run again.")
        return 0

    files = find_media(inbox)
    if not files:
        print(f"no media files in {inbox}/")
        return 0

    options = Options(outdir=args.outdir, model=args.model)
    failed = []
    for number, path in enumerate(files, start=1):
        print(f"\n[{number}/{len(files)}] {path.name}")
        try:
            result = run(str(path), options)
        except Exception as error:
            print(f"  failed: {error}", file=sys.stderr)
            failed.append(path)
            continue
        print(f"  -> {result.paths[-1]}")
        if not args.keep:
            move_to_done(path, inbox / DONE_DIRNAME)

    print(f"\n{len(files) - len(failed)}/{len(files)} files transcribed.")
    for path in failed:
        print(f"  failed: {path}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
