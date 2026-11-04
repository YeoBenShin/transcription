"""Stage: get the title of a YouTube video from the public oEmbed endpoint."""

from typing import Optional

import requests

from app.stages.common.http import TIMEOUT, USER_AGENT

OEMBED = "https://www.youtube.com/oembed"


def fetch_video_title(video_id: str) -> Optional[str]:
    """Return the video title, or None on a network or response error."""
    try:
        response = requests.get(
            OEMBED,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        title = response.json().get("title")
    except (requests.RequestException, ValueError) as error:
        print(f"no video title ({type(error).__name__})")
        return None
    if title:
        print(f"youtube video: {title!r}")
    return title or None
