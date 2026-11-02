"""YouTube -> chunked transcript .txt files.

Usage:
    python pipeline.py <youtube-url> [--chunk-minutes 4] [--outdir out] [--model small]

Step 1: try youtube-transcript-api (English, translating if needed).
Step 2: fall back to downloading the audio with yt-dlp.
Step 3: transcribe locally with faster-whisper.
Step 4: split into fixed-length chunks and write one .txt per chunk.

Steps 5 and 6 (per-chunk summaries and the overall summary) are left to you:
hand the generated .txt files to the model yourself.
"""

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

TARGET_LANGUAGE = "en"

_VIDEO_ID_PATTERNS = [
    r"(?:v=|/videos/|embed/|shorts/|live/|/v/)([0-9A-Za-z_-]{11})",
    r"youtu\.be/([0-9A-Za-z_-]{11})",
]


@dataclass
class Line:
    start: float  # seconds from the beginning of the video
    text: str


def parse_video_id(url_or_id: str) -> str:
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url_or_id):
        return url_or_id
    for pattern in _VIDEO_ID_PATTERNS:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    raise ValueError(f"could not find a video id in {url_or_id!r}")


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def clean(text: str) -> str:
    return " ".join(text.split())


def fetch_captions(video_id: str) -> Optional[List[Line]]:
    """Step 1: YouTube's own captions, translated to English if necessary."""
    api = YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript([TARGET_LANGUAGE])
        except CouldNotRetrieveTranscript:
            transcript = next(
                (t for t in transcript_list if t.is_translatable), None
            )
            if transcript is None:
                return None
            transcript = transcript.translate(TARGET_LANGUAGE)
        fetched = transcript.fetch()
    except CouldNotRetrieveTranscript as error:
        print(f"no usable captions ({type(error).__name__}), falling back to audio")
        return None

    source = "auto-generated" if fetched.is_generated else "human-written"
    print(f"using {source} {fetched.language_code} captions ({len(fetched)} snippets)")
    return [Line(s.start, clean(s.text)) for s in fetched if clean(s.text)]


def download_audio(video_id: str, directory: Path) -> Path:
    """Step 2: grab the audio track as-is, no re-encoding so ffmpeg isn't needed."""
    from yt_dlp import YoutubeDL

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    print("downloading audio with yt-dlp")
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")
    return Path(ydl.prepare_filename(info))


def transcribe(audio: Path, model_size: str) -> List[Line]:
    """Step 3: local speech-to-text."""
    from faster_whisper import WhisperModel

    print(f"transcribing with faster-whisper ({model_size}, cpu/int8)")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language=TARGET_LANGUAGE, vad_filter=True)
    lines = []
    for segment in segments:
        text = clean(segment.text)
        if text:
            lines.append(Line(segment.start, text))
            print(f"  [{timestamp(segment.start)}] {text[:70]}", end="\r", flush=True)
    print()
    return lines


def chunk(lines: List[Line], chunk_seconds: float) -> List[List[Line]]:
    """Step 4: bucket lines by which chunk_seconds window their start falls in."""
    if not lines:
        return []
    chunks: List[List[Line]] = []
    for line in lines:
        index = int(line.start // chunk_seconds)
        while len(chunks) <= index:
            chunks.append([])
        chunks[index].append(line)
    return [c for c in chunks if c]


def write_chunks(
    chunks: List[List[Line]], video_id: str, chunk_seconds: float, outdir: Path
) -> List[Path]:
    directory = outdir / video_id
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("chunk_*.txt"):
        stale.unlink()

    paths = []
    for number, lines in enumerate(chunks, start=1):
        index = int(lines[0].start // chunk_seconds)
        start, end = index * chunk_seconds, (index + 1) * chunk_seconds
        path = directory / f"chunk_{number:03d}_{timestamp(start)}-{timestamp(end)}.txt".replace(":", "")
        header = (
            f"# {video_id} chunk {number}/{len(chunks)} "
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="YouTube URL or 11-character video id")
    parser.add_argument("--chunk-minutes", type=float, default=4.0)
    parser.add_argument("--outdir", type=Path, default=Path("out"))
    parser.add_argument(
        "--model", default="small", help="faster-whisper model used for the fallback"
    )
    args = parser.parse_args()

    video_id = parse_video_id(args.url)
    chunk_seconds = args.chunk_minutes * 60

    lines = fetch_captions(video_id)
    if not lines:
        with tempfile.TemporaryDirectory() as tmp:
            audio = download_audio(video_id, Path(tmp))
            lines = transcribe(audio, args.model)

    if not lines:
        print("no transcript could be produced", file=sys.stderr)
        return 1

    chunks = chunk(lines, chunk_seconds)
    paths = write_chunks(chunks, video_id, chunk_seconds, args.outdir)

    duration = lines[-1].start
    print(
        f"\n{len(lines)} lines, ~{timestamp(duration)} of video, "
        f"{len(chunks)} chunks of {args.chunk_minutes:g} min:"
    )
    for path in paths:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
