"""Stage: read public metadata of a Spotify podcast episode from its embed page."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from app.stages.http import USER_AGENT_BROWSER, TIMEOUT

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


@dataclass
class SpotifyEpisode:
    episode_id: str
    title: str
    show: str
    released: Optional[datetime]
    duration_seconds: Optional[float]


def fetch_spotify_episode(episode_id: str) -> SpotifyEpisode:
    """Return title, show name, release time and duration of a Spotify episode."""
    response = requests.get(
        f"https://open.spotify.com/embed/episode/{episode_id}",
        headers={"User-Agent": USER_AGENT_BROWSER},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    match = _NEXT_DATA.search(response.text)
    if match is None:
        raise RuntimeError(f"no episode data on Spotify embed page for {episode_id}")
    entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    if entity.get("type") != "episode":
        raise RuntimeError(f"Spotify id {episode_id} is a {entity.get('type')}, not an episode")

    iso = (entity.get("releaseDate") or {}).get("isoString")
    released = datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None
    duration_ms = entity.get("duration")
    episode = SpotifyEpisode(
        episode_id=episode_id,
        title=entity.get("title") or entity["name"],
        show=entity["subtitle"],
        released=released,
        duration_seconds=duration_ms / 1000 if duration_ms else None,
    )
    print(f"spotify episode: {episode.title!r} from {episode.show!r}")
    return episode
