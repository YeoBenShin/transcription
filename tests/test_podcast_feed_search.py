"""Feed URL order and failure handling of podcast_feed.search_feed_urls, with Apple search replaced by fixed results."""

import requests

from app.stages.spotify import podcast_feed

SHOW = "The Acquirers Podcast"
TITLE = "Why Bill Ackman Loves Uber at 16x Earnings + Our 2 Favorite Cheap Stocks"


def episode(collection: str, track: str, feed: str) -> dict:
    return {"kind": "podcast-episode", "collectionName": collection, "trackName": track, "feedUrl": feed}


def show(collection: str, feed: str) -> dict:
    return {"kind": "podcast", "collectionName": collection, "trackName": collection, "feedUrl": feed}


def fake_search(results: dict):
    """Return an _apple_search replacement that gives results[(entity, term)], or [] when absent."""
    calls = []

    def search(term: str, entity: str):
        calls.append((entity, term))
        return results.get((entity, term), [])

    search.calls = calls
    return search


def test_feed_with_episode_title_comes_first(monkeypatch):
    search = fake_search({
        ("podcast", SHOW): [show("Acquired", "https://a/acquired"), show("Acquired Taste", "https://a/taste")],
        ("podcastEpisode", TITLE): [
            episode("Other Show", "Bill Ackman talks", "https://a/other"),
            episode("The Acquirers Podcast", TITLE, "https://a/acquirers"),
        ],
    })
    monkeypatch.setattr(podcast_feed, "_apple_search", search)
    urls = podcast_feed.search_feed_urls(SHOW, TITLE)
    assert urls[0] == "https://a/acquirers"
    assert set(urls) == {"https://a/acquirers", "https://a/other", "https://a/acquired", "https://a/taste"}
    assert search.calls == [("podcastEpisode", TITLE), ("podcast", SHOW), ("podcastEpisode", SHOW)]


def test_show_name_match_comes_before_other_shows(monkeypatch):
    monkeypatch.setattr(podcast_feed, "_apple_search", fake_search({
        ("podcast", SHOW): [show("Acquired", "https://a/acquired")],
        ("podcastEpisode", SHOW): [episode("The Acquirers Podcast", "Some episode", "https://a/acquirers")],
    }))
    assert podcast_feed.search_feed_urls(SHOW, TITLE) == ["https://a/acquirers", "https://a/acquired"]


def test_show_result_named_like_title_is_not_an_episode_match(monkeypatch):
    monkeypatch.setattr(podcast_feed, "_apple_search", fake_search({
        ("podcast", SHOW): [show(TITLE, "https://a/same-name-show"), show(SHOW, "https://a/acquirers")],
    }))
    assert podcast_feed.search_feed_urls(SHOW, TITLE) == ["https://a/acquirers", "https://a/same-name-show"]


def test_same_feed_listed_once_and_at_most_max_feeds(monkeypatch):
    many = [show(f"Show {i}", f"https://a/{i}") for i in range(10)]
    monkeypatch.setattr(podcast_feed, "_apple_search", fake_search({
        ("podcast", SHOW): many,
        ("podcastEpisode", SHOW): many,
    }))
    urls = podcast_feed.search_feed_urls(SHOW, TITLE)
    assert urls == [f"https://a/{i}" for i in range(podcast_feed.MAX_FEEDS)]


def test_failed_search_gives_no_results(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(podcast_feed.requests, "get", fail)
    assert podcast_feed.search_feed_urls(SHOW, TITLE) == []
