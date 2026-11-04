# stages/

## How it works

Each stage does one thing. Stages are grouped by which sources use them:

| Folder | Used by | Stages |
|---|---|---|
| `youtube/` | `sources/youtube.py` only | `video_title`, `captions`, `download` |
| `spotify/` | `sources/spotify.py` only | `spotify_episode`, `podcast_feed`, `feed_transcript` |
| `common/` | more than one source, or `pipeline.py` for all sources | `http`, `transcribe`, `chunk`, `write` |

Import rules:
- Stages do not import from `sources/` or `pipeline.py`.
- `youtube/` and `spotify/` stages can import from `common/`. They do not import from each other.
- `common/` stages do not import from `youtube/` or `spotify/`.

Details: `youtube/ARCHITECTURE.md`, `spotify/ARCHITECTURE.md`, `common/ARCHITECTURE.md`.

## Decisions

### Grouping by source
- The folder shows which sources depend on a stage. A change to a stage in `youtube/` or `spotify/` affects only that source. A change to a stage in `common/` can affect all sources.
- A stage goes in a source folder when only that source calls it, even if the stage could work for other inputs (for example `youtube/download` takes any yt-dlp URL). When a second source starts to use it, move it to `common/`.
