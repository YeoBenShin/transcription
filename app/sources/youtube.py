"""Source: full YouTube video URL. Captions first, then audio + Whisper."""

import re
from typing import Optional

from app.models import Options, Transcript, output_name
from app.stages.common.audio_file import find_audio
from app.stages.common.retry import retry
from app.stages.youtube.captions import fetch_captions
from app.stages.youtube.download import download_audio
from app.stages.common.transcribe import partial_path, transcribe
from app.stages.youtube.video_info import fetch_video_info

_URL_PREFIX = "https://www."
_HOST_NAMES = ("youtube", "youtu")
_ID = re.compile(r"[0-9A-Za-z_-]{11}")


def parse_video_id(raw: str) -> Optional[str]:
    """Return the video id at the end of a full YouTube URL, or None.

    The URL must start with https://www. and the host name must be youtube or youtu.be.
    Text from the first '&' is removed (playlist and other extra parameters).
    The id is the text after the last '=' or '/'.
    """
    if not raw.startswith(_URL_PREFIX):
        return None
    rest = raw[len(_URL_PREFIX):].split("&", 1)[0]
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
        info = fetch_video_info(video_id)
        name = output_name(info.published, info.channel, info.title, video_id)

        lines = fetch_captions(video_id)
        if lines:
            return Transcript(video_id, lines, origin="youtube captions", name=name)

        print("falling back to audio")
        temp = options.temp_dir
        url = f"https://www.youtube.com/watch?v={video_id}"

        def download_and_transcribe():
            temp.mkdir(parents=True, exist_ok=True)
            audio = find_audio(temp, name) or download_audio(url, temp, name)
            return audio, transcribe(audio, options.model, partial_path(temp, name), video_id)

        audio, lines = retry(download_and_transcribe)
        if not lines:
            return None
        return Transcript(video_id, lines, origin="faster-whisper", name=name, downloaded_audio=audio)
