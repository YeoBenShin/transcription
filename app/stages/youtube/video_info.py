"""Stage: get the upload date, channel and title of a YouTube video."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import requests

from app.stages.common.http import TIMEOUT, USER_AGENT

OEMBED = "https://www.youtube.com/oembed"


@dataclass
class VideoInfo:
    published: Optional[date]
    channel: Optional[str]
    title: Optional[str]


def fetch_video_info(video_id: str) -> VideoInfo:
    """Return upload date, channel and title from yt-dlp metadata (no download).

    If yt-dlp fails, return channel and title from the oEmbed endpoint and no date.
    If that also fails, return all fields as None.
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    info = _from_yt_dlp(url) or _from_oembed(url)
    print(f"youtube video: {info.published} {info.channel!r} {info.title!r}")
    return info


def _from_yt_dlp(url: str) -> Optional[VideoInfo]:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import YoutubeDLError

    options = {"quiet": True, "no_warnings": True, "skip_download": True, "ignore_no_formats_error": True}
    try:
        with YoutubeDL(options) as ydl:
            data = ydl.extract_info(url, download=False, process=False)
    except YoutubeDLError as error:
        print(f"no yt-dlp metadata ({type(error).__name__})")
        return None
    raw_date = data.get("upload_date")
    try:
        published = datetime.strptime(raw_date, "%Y%m%d").date() if raw_date else None
    except ValueError:
        published = None
    return VideoInfo(published, data.get("channel") or data.get("uploader"), data.get("title"))


def _from_oembed(url: str) -> VideoInfo:
    try:
        response = requests.get(
            OEMBED,
            params={"url": url, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as error:
        print(f"no oEmbed metadata ({type(error).__name__})")
        return VideoInfo(None, None, None)
    return VideoInfo(None, data.get("author_name") or None, data.get("title") or None)
