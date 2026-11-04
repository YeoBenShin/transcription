"""Stage: find a show's public RSS feed with the Apple Podcasts search API, then find one episode in it."""

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import List, Optional

import requests
from defusedxml import ElementTree

from app.stages.common.http import TIMEOUT, USER_AGENT

APPLE_SEARCH = "https://itunes.apple.com/search"
MAX_FEEDS = 5
DATE_WINDOW = timedelta(days=2)
MIN_TITLE_SIMILARITY = 0.6


@dataclass
class TranscriptLink:
    url: str
    mime_type: str


@dataclass
class FeedEpisode:
    feed_url: str
    title: str
    enclosure_url: Optional[str]
    transcripts: List[TranscriptLink] = field(default_factory=list)


def normalize(text: str) -> str:
    """Lowercase, decode HTML entities, keep only letters and digits separated by single spaces."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", html.unescape(text).lower()).split())


def search_feed_urls(show: str) -> List[str]:
    """Return feed URLs of Apple Podcasts search results. Results with the same show name come first."""
    response = requests.get(
        APPLE_SEARCH,
        params={"term": show, "media": "podcast", "entity": "podcast", "limit": 10},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    results = [r for r in response.json().get("results", []) if r.get("feedUrl")]
    wanted = normalize(show)
    results.sort(key=lambda r: normalize(r.get("collectionName", "")) != wanted)
    urls: List[str] = []
    for result in results:
        if result["feedUrl"] not in urls:
            urls.append(result["feedUrl"])
    return urls[:MAX_FEEDS]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _published(item) -> Optional[datetime]:
    raw = item.findtext("pubDate")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError):
        return None


def _to_episode(feed_url: str, item) -> FeedEpisode:
    enclosure = item.find("enclosure")
    transcripts = [
        TranscriptLink(child.get("url"), (child.get("type") or "").lower())
        for child in item
        if _local(child.tag) == "transcript" and child.get("url")
    ]
    return FeedEpisode(
        feed_url=feed_url,
        title=(item.findtext("title") or "").strip(),
        enclosure_url=enclosure.get("url") if enclosure is not None else None,
        transcripts=transcripts,
    )


def match_item(items: list, title: str, released: Optional[datetime]):
    """Return the feed item for the episode, or None.

    First an exact match of the normalized title. Then, among items published within
    DATE_WINDOW of `released`, the one with the most similar title, if the similarity
    is at least MIN_TITLE_SIMILARITY.
    """
    wanted = normalize(title)
    for item in items:
        if normalize(item.findtext("title") or "") == wanted:
            return item
    if released is None:
        return None
    best, best_score = None, MIN_TITLE_SIMILARITY
    for item in items:
        published = _published(item)
        if published is None or abs(published - released) > DATE_WINDOW:
            continue
        score = SequenceMatcher(None, normalize(item.findtext("title") or ""), wanted).ratio()
        if score >= best_score:
            best, best_score = item, score
    return best


def find_feed_episode(show: str, title: str, released: Optional[datetime]) -> Optional[FeedEpisode]:
    """Search feeds for `show` and return the first episode that matches `title` and `released`."""
    feed_urls = search_feed_urls(show)
    if not feed_urls:
        print(f"no RSS feed found for {show!r}")
        return None
    for feed_url in feed_urls:
        print(f"checking feed {feed_url}")
        try:
            response = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            response.raise_for_status()
            channel = ElementTree.fromstring(response.content).find("channel")
        except (requests.RequestException, ElementTree.ParseError) as error:
            print(f"  skipped ({type(error).__name__})")
            continue
        if channel is None:
            continue
        item = match_item(channel.findall("item"), title, released)
        if item is not None:
            episode = _to_episode(feed_url, item)
            print(f"  found episode {episode.title!r}")
            return episode
    print("episode not found in any feed")
    return None
