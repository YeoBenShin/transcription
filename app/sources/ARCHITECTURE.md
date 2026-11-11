# sources/

## How it works

Each source implements the `Source` protocol in `base.py`:
- `name`: shown to the user.
- `matches(raw) -> bool`: cheap check, no network calls.
- `get_transcript(raw, options) -> Transcript | None`: runs the stages for this input type.

`__init__.py` holds `SOURCES`, the ordered list that the router tries.

| Source | Matches | Stages |
|---|---|---|
| `LocalFileSource` | path to a file that exists | transcribe |
| `YouTubeSource` | full URL that starts with `https://www.youtube.` or `https://www.youtu.be`, with the 11-character id after the last `=` or `/`; text from the first `&` is removed | captions; if none: download, then transcribe |
| `SpotifySource` | `https://open.spotify.com/episode/<22-char id>`, optional `intl-xx/` and query string | spotify_episode, podcast_feed; then feed_transcript if the item has a timestamped transcript; else `http.download_file` of the enclosure, then transcribe |

To add a source: make a module with a class that has the 3 members above, then add an instance to `SOURCES`.

## Decisions

- `LocalFileSource` is first in `SOURCES`. A file name can look like a YouTube id (11 characters), and a file that exists on disk is the stronger signal.
- The YouTube source accepts only the full URL copied from the browser address bar (`/watch?v=ID` or `/shorts/ID`). Both forms end with the id, so the id is the text after the last `=` or `/`. This replaces a set of regexes for many URL forms, which was hard to read. A bare id and URLs without `https://www.` are not accepted.
- The YouTube source removes text from the first `&` before it finds the id. A video opened from a playlist has `&list=...&index=N` after the id, and other parameters such as `&t=` also come after `v=`.
- The host name check (`youtube` or `youtu`) stops a non-YouTube URL that ends in 11 characters from going to the YouTube source.
- `matches` must not do network calls. Routing must be fast, and it must give the same result offline.
- Spotify does not give the audio or a transcript without a login. Most shows on Spotify also publish a public RSS feed, so the Spotify source uses Spotify only for the episode title and show name, and gets everything else from the feed.
- The Spotify source accepts episode URLs only. A show URL does not identify one episode.
- The Spotify source tries the feed transcript before Whisper, for the same reason as YouTube captions: it is free and fast, and the publisher made it from the real audio.
- The YouTube and Spotify sources download audio as `audio.<ext>` into the output folder. If a completed `audio.<ext>` is already there, they use it and do not download again. `pipeline.run` deletes it after `full.txt` is written. The local file source never deletes the input file.
- The Whisper path (download + transcribe) runs inside `retry`. Caption and feed-transcript paths do not retry.
