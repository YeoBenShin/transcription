"""Stage: fetch YouTube's own captions, translated to English if necessary."""

from typing import List, Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

from app.models import TARGET_LANGUAGE, Line, clean


def fetch_captions(video_id: str) -> Optional[List[Line]]:
    """Return caption lines, or None if the video has no usable captions."""
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
        print(f"no usable captions ({type(error).__name__})")
        return None

    kind = "auto-generated" if fetched.is_generated else "human-written"
    print(f"using {kind} {fetched.language_code} captions ({len(fetched)} snippets)")
    return [Line(s.start, clean(s.text)) for s in fetched if clean(s.text)]
