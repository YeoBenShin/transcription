"""Source: full YouTube video URL. Captions first, then audio + Whisper."""

import re
import tempfile
from pathlib import Path
from typing import Optional

from app.models import Options, Transcript
from app.stages.youtube.captions import fetch_captions
from app.stages.youtube.download import download_audio
from app.stages.common.transcribe import transcribe
from app.stages.youtube.video_title import fetch_video_title

_URL_PREFIX = "https://www."
_HOST_NAMES = ("youtube", "youtu")
_ID = re.compile(r"[0-9A-Za-z_-]{11}")


def parse_video_id(raw: str) -> Optional[str]:
    """Return the video id at the end of a full YouTube URL, or None.

    The URL must start with https://www. and the host name must be youtube or youtu.be.
    The id is the text after the last '=' or '/'.
    """
    if not raw.startswith(_URL_PREFIX):
        return None
    rest = raw[len(_URL_PREFIX):]
    if rest.split(".", 1)[0] not in _HOST_NAMES:
        return None
    video_id = re.split(r"[=/]", rest)[-1]
    if not _ID.fullmatch(video_id):
        return None
    return video_id


class YouTubeSource:
    name = "youtube"

    def matches(self, raw: str) -> bool:
        return parse_video_id(raw) is not None

    def get_transcript(self, raw: str, options: Options) -> Optional[Transcript]:
        video_id = parse_video_id(raw)
        assert video_id is not None
        title = fetch_video_title(video_id)

        lines = fetch_captions(video_id)
        if lines:
            return Transcript(video_id, lines, origin="youtube captions", title=title)

        print("falling back to audio")
        with tempfile.TemporaryDirectory() as tmp:
            audio = download_audio(f"https://www.youtube.com/watch?v={video_id}", Path(tmp))
            lines = transcribe(audio, options.model)
        if not lines:
            return None
        return Transcript(video_id, lines, origin="faster-whisper", title=title)
