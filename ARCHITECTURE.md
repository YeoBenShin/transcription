# Architecture

## How it works

```
input string
   │
   ▼
cli.py ──► pipeline.run(raw, Options)
              │
              ├─ route(raw): first source in SOURCES with matches(raw) == True
              │
              ├─ source.get_transcript(raw, options) ──► Transcript(source_id, lines, origin)
              │      (the source calls the acquisition stages it needs)
              │
              ├─ stages.chunk   (same for all sources)
              └─ stages.write   (same for all sources)
```

- `sources/` decides *what* to do for an input type.
- `stages/` does *one* thing each. Stages do not know about sources.
- Every source produces the same `Transcript` type. Everything after acquisition does not depend on the input type.

Details: `app/ARCHITECTURE.md`, `app/sources/ARCHITECTURE.md`, `app/stages/ARCHITECTURE.md`.

## Decisions

### One interface, routing by input
- The user gives one string. The pipeline finds the input type, so the user does not need to select a subcommand per platform.
- To add a new input type (for example a podcast RSS feed or Spotify), add one source module and register it in `SOURCES`. The CLI and the later stages do not change.

### Sources orchestrate, stages are reusable
- Fallback logic (captions, then audio, then Whisper) belongs to the source, because each input type has a different fallback order.
- Stages take plain arguments (a URL, a path, lines). Thus a new source can reuse `download` and `transcribe` without changes.

### Package, not a single script
- Run as `python -m app`. The package folder is `app/`, not `transcription/`, so that it is not confused with the project folder. The earlier entry point `python pipeline.py <url>` is removed.
