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
| `YouTubeSource` | full URL that starts with `https://www.youtube.` or `https://www.youtu.be`, with the 11-character id after the last `=` or `/` | captions; if none: download, then transcribe |

To add a source: make a module with a class that has the 3 members above, then add an instance to `SOURCES`.

## Decisions

- `LocalFileSource` is first in `SOURCES`. A file name can look like a YouTube id (11 characters), and a file that exists on disk is the stronger signal.
- The YouTube source accepts only the full URL copied from the browser address bar (`/watch?v=ID` or `/shorts/ID`). Both forms end with the id, so the id is the text after the last `=` or `/`. This replaces a set of regexes for many URL forms, which was hard to read. A bare id and URLs without `https://www.` are not accepted.
- The host name check (`youtube` or `youtu`) stops a non-YouTube URL that ends in 11 characters from going to the YouTube source.
- `matches` must not do network calls. Routing must be fast, and it must give the same result offline.
- The YouTube source downloads audio into a temporary folder that it deletes after transcription. Only the text output is kept.
