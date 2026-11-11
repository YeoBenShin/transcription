# stages/spotify/

## How it works

Stages that only the Spotify source calls. `sources/spotify.py` calls `spotify_episode` for metadata, `podcast_feed` to find the episode in the show's RSS feed, then `feed_transcript`. If the feed has no timestamped transcript it calls `common.http.download_file` on the enclosure and then `common.transcribe`.

| Stage | Input | Output |
|---|---|---|
| `spotify_episode.fetch_spotify_episode` | Spotify episode id | `SpotifyEpisode` (title, show, release time, duration) |
| `podcast_feed.find_feed_episode` | show name, episode title, release time | `FeedEpisode` (feed URL, enclosure URL, transcript links), or `None` |
| `feed_transcript.pick_transcript` | transcript links | best timestamped link, or `None` |
| `feed_transcript.fetch_feed_transcript` | transcript link | `List[Line]`, or `None` |

## Decisions

### spotify_episode
- Reads the JSON in the `__NEXT_DATA__` script tag of `open.spotify.com/embed/episode/<id>`. This page needs no login and has the title, show name and release time. The Spotify Web API needs app credentials, and the oEmbed endpoint has no show name.
- Sends a browser User-Agent. The embed page is made for browsers.

### podcast_feed
- Finds feeds with the Apple Podcasts search API (`itunes.apple.com/search`). It needs no key. Podcast Index also has a search API, but it needs a key and secret. Listen Notes and Podchaser also need a key. fyyd and gpodder.net need no key, but did not find shows that Apple has.
- Three Apple searches: episodes by episode title, shows by show name, episodes by show name. A show search alone can miss the show: `The Acquirers Podcast` gave only "Acquired..." shows in the top 10, but `The Acquirer's Podcast` gave the correct show first. The episode-title search found it first. The Spotify embed page has no publisher name, so a search by publisher is not possible.
- One search can give many results (copies, summary shows). Order: feeds with an episode whose normalized title is the Spotify title, then feeds with the same normalized show name, then the rest. A feed is used only if it contains the episode. At most `MAX_FEEDS` feeds are fetched.
- A failed search gives no results and the other searches continue.
- Episode match: exact normalized title first (case, punctuation and HTML entities ignored, because feeds encode `&` as `&#038;` and Spotify does not). If no exact match: the most similar title among items published within 2 days of the Spotify release time.
- Feeds are parsed with `defusedxml`, because the XML comes from third parties.

### feed_transcript
- Reads the Podcasting 2.0 `<podcast:transcript>` tag. Preference: JSON, then WebVTT, then SRT. HTML and plain-text transcripts have no timestamps, so chunking cannot use them; they are ignored and Whisper runs.
- JSON transcripts are often one segment per word. Words are grouped into lines at a speaker change, a pause over 2 s, a sentence end after 5 s, or 30 s. Each line gets a `Speaker: ` prefix when the JSON names the speaker, so a chunk read alone still shows who talks.
- SRT/VTT files are decoded as UTF-8. HTTP servers often send them without a charset, and `requests` then assumes Latin-1.
