# stages/

## How it works

Each stage does one thing. Stages are grouped by which sources use them:

| Folder | Used by | Stages |
|---|---|---|
| `youtube/` | `sources/youtube.py` only | `video_info`, `captions`, `download` |
| `local/` | `sources/local_file.py` only | `file_info` |
| `spotify/` | `sources/spotify.py` only | `spotify_episode`, `podcast_feed`, `feed_transcript` |
| `common/` | more than one source, or `pipeline.py` for all sources | `http`, `transcribe`, `chunk` (not used now), `write` |

Import rules:
- Stages do not import from `sources/` or `pipeline.py`.
- `youtube/`, `spotify/` and `local/` stages can import from `common/`. They do not import from each other.
- `common/` stages do not import from `youtube/`, `spotify/` or `local/`.

Details: `youtube/ARCHITECTURE.md`, `spotify/ARCHITECTURE.md`, `local/ARCHITECTURE.md`, `common/ARCHITECTURE.md`.

## Decisions

### Grouping by source
- The folder shows which sources depend on a stage. A change to a stage in `youtube/` or `spotify/` affects only that source. A change to a stage in `common/` can affect all sources.
- A stage goes in a source folder when only that source calls it, even if the stage could work for other inputs (for example `youtube/download` takes any yt-dlp URL). When a second source starts to use it, move it to `common/`.
