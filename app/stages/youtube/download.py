"""Stage: download the audio track of an online media URL with yt-dlp."""

from pathlib import Path


def download_audio(url: str, directory: Path, stem: str) -> Path:
    """Save the best audio stream of `url` as directory/stem.<ext> without re-encoding.

    yt-dlp writes to a .part file and renames it when the download is complete.
    '%' in the path is escaped, because yt-dlp reads it as a template field.
    """
    from yt_dlp import YoutubeDL

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(directory / stem).replace("%", "%%") + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    print("downloading audio with yt-dlp")
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url)
    return Path(ydl.prepare_filename(info))
