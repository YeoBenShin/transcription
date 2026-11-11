"""Source: Spotify podcast episode URL. Feed transcript first, then feed audio + Whisper."""

import re
from typing import Optional

import requests

from app.models import Options, Transcript, output_name
from app.stages.common.audio_file import find_audio
from app.stages.common.retry import retry
from app.stages.common import temp_files
from app.stages.spotify.feed_transcript import fetch_feed_transcript, pick_transcript
from app.stages.common.http import download_file
from app.stages.spotify.podcast_feed import find_feed_episode
from app.stages.spotify.spotify_episode import fetch_spotify_episode
from app.stages.common.transcribe import partial_path, transcribe

_URL = re.compile(r"https://open\.spotify\.com/(?:intl-[a-z-]+/)?episode/([0-9A-Za-z]{22})(?:[?#].*)?")


def parse_episode_id(raw: str) -> Optional[str]:
    """Return the episode id of an https://open.spotify.com/episode/<id> URL, or None."""
    match = _URL.fullmatch(raw.strip())
    return match.group(1) if match else None


class SpotifySource:
    name = "spotify episode"

    def matches(self, raw: str) -> bool:
        return parse_episode_id(raw) is not None

    def get_transcript(self, raw: str, options: Options) -> Optional[Transcript]:
        episode_id = parse_episode_id(raw)
        assert episode_id is not None

        try:
            spotify = fetch_spotify_episode(episode_id)
            episode = find_feed_episode(spotify.show, spotify.title, spotify.released)
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
            print(f"episode lookup failed ({type(error).__name__}: {error})")
            return None
        if episode is None:
            return None

        published = spotify.released.date() if spotify.released else None
        name = output_name(published, spotify.show, spotify.title, episode_id)

        link = pick_transcript(episode.transcripts)
        if link is not None:
            lines = fetch_feed_transcript(link)
            if lines:
                return Transcript(
                    episode_id, lines, origin=f"feed transcript ({link.mime_type})", name=name
                )

        if not episode.enclosure_url:
            print("feed item has no audio enclosure")
            return None
        print("falling back to audio")
        temp_files.announce(name)
        temp = options.temp_dir

        def download_and_transcribe():
            temp.mkdir(parents=True, exist_ok=True)
            audio = find_audio(temp, name) or download_file(episode.enclosure_url, temp, name)
            return audio, transcribe(audio, options.model, partial_path(temp, name), episode_id)

        audio, lines = retry(download_and_transcribe)
        if not lines:
            return None
        return Transcript(
            episode_id, lines, origin="faster-whisper", name=name, downloaded_audio=audio
        )
