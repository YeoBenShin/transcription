"""Stage: download the audio track of an online media URL with yt-dlp."""

from pathlib import Path


def download_audio(url: str, directory: Path) -> Path:
    """Save the best audio stream of `url` into `directory` without re-encoding."""
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
        info = ydl.extract_info(url)
    return Path(ydl.prepare_filename(info))
