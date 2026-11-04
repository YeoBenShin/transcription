"""Stage: fetch a Podcasting 2.0 <podcast:transcript> file and parse it into timestamped lines."""

import re
from typing import List, Optional

import requests

from app.models import Line, clean
from app.stages.http import TIMEOUT, USER_AGENT
from app.stages.podcast_feed import TranscriptLink

# Formats with timestamps, best first. HTML and plain text have no timestamps and are not used.
_PREFERENCE = {
    "application/json": 0,
    "text/vtt": 1,
    "application/x-subrip": 2,
    "application/srt": 2,
    "text/srt": 2,
}
_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")
_VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")
_TAG = re.compile(r"<[^>]+>")

# JSON word grouping: start a new line on a speaker change, a pause, or a long line.
_MAX_GAP = 2.0
_MAX_LINE_SECONDS = 30.0
_MIN_SENTENCE_SECONDS = 5.0


def pick_transcript(links: List[TranscriptLink]) -> Optional[TranscriptLink]:
    """Return the timestamped transcript link with the best format, or None."""
    usable = [l for l in links if l.mime_type in _PREFERENCE]
    return min(usable, key=lambda l: _PREFERENCE[l.mime_type]) if usable else None


def _cue_start(match: re.Match) -> float:
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def parse_cues(text: str) -> List[Line]:
    """Parse SRT or WebVTT. Each cue becomes one line. A WebVTT voice tag becomes a 'Speaker: ' prefix."""
    lines: List[Line] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        rows = block.strip().split("\n")
        for index, row in enumerate(rows):
            match = _CUE.match(row.strip())
            if match:
                body = " ".join(rows[index + 1:])
                body = clean(_TAG.sub("", _VOICE.sub(r"\1: ", body)))
                if body:
                    lines.append(Line(_cue_start(match), body))
                break
    return lines


def parse_json(data: dict) -> List[Line]:
    """Parse Podcasting 2.0 JSON segments. Group word-level segments into lines, with a 'Speaker: ' prefix."""
    lines: List[Line] = []
    words: List[str] = []
    speaker, start, end = None, 0.0, 0.0

    def flush():
        text = clean(" ".join(words))
        if text:
            lines.append(Line(start, f"{speaker}: {text}" if speaker else text))
        words.clear()

    for segment in data.get("segments", []):
        body = clean(str(segment.get("body", "")))
        if not body:
            continue
        seg_start = float(segment.get("startTime", end))
        seg_speaker = segment.get("speaker")
        if words:
            sentence_done = words[-1][-1] in ".?!" and end - start >= _MIN_SENTENCE_SECONDS
            if (
                seg_speaker != speaker
                or seg_start - end > _MAX_GAP
                or seg_start - start > _MAX_LINE_SECONDS
                or sentence_done
            ):
                flush()
        if not words:
            speaker, start = seg_speaker, seg_start
        words.append(body)
        end = float(segment.get("endTime", seg_start))
    flush()
    return lines


def fetch_feed_transcript(link: TranscriptLink) -> Optional[List[Line]]:
    """Download and parse one transcript link. Return None on a network or parse failure."""
    print(f"using feed transcript ({link.mime_type}) {link.url}")
    try:
        response = requests.get(link.url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        response.raise_for_status()
        if link.mime_type == "application/json":
            lines = parse_json(response.json())
        else:
            lines = parse_cues(response.content.decode("utf-8-sig", errors="replace"))
    except (requests.RequestException, ValueError) as error:
        print(f"feed transcript failed ({type(error).__name__})")
        return None
    return lines or None
