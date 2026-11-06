# Architecture

## How it works

```
input string                 to-be-transcribe/*.m4a ...
   │                                 │
   ▼                                 ▼
cli.py ──► pipeline.run(raw, Options) ◄── batch.py (one call per file)
              │
              ├─ route(raw): first source in SOURCES with matches(raw) == True
              │
              ├─ source.get_transcript(raw, options) ──► Transcript(source_id, lines, origin, title)
              │      (the source calls the acquisition stages it needs)
              │
              └─ stages.common.write   (same for all sources)
```

- `sources/` decides *what* to do for an input type.
- `stages/` does *one* thing each, grouped by which source uses it (`youtube/`, `spotify/`, `common/`). Stages do not import sources.
- Every source produces the same `Transcript` type. Everything after acquisition does not depend on the input type.

Details: `app/ARCHITECTURE.md`, `app/sources/ARCHITECTURE.md`, `app/stages/ARCHITECTURE.md`.

## Decisions

### One interface, routing by input
- The user gives one string. The pipeline finds the input type, so the user does not need to select a subcommand per platform.
- To add a new input type (for example a plain podcast RSS feed), add one source module and register it in `SOURCES`. The CLI and the later stages do not change.

### Sources orchestrate, stages are reusable
- Fallback logic (captions, then audio, then Whisper) belongs to the source, because each input type has a different fallback order.
- Stages take plain arguments (a URL, a path, lines). Thus a new source can reuse `download` and `transcribe` without changes.

### Whisper progress is saved and resumed
- A long Whisper run on CPU can take hours. Before, all lines were only in memory until the end, so a crash lost all the work.
- Each segment is appended to `full.partial.jsonl` in the output folder as it is made. A run of the same input continues from the `end` time of the last saved line. Line text is not used to find the resume point: Whisper gives different text and segment boundaries when it starts at a different point, so text matching is not reliable.
- The audio is cut exactly at the last `end`, with no overlap. An overlap gives duplicate lines, and a segment that crosses the cut point cannot be split without word timestamps. The last 5 lines go to Whisper as `initial_prompt` for context.
- The header line (model, source id, audio size, audio sample count) must be equal, or the progress is discarded. For a local file the source id is only the file name, so the audio size and length are what detect a different file with the same name.
- Downloaded audio is kept in the output folder, not a temp folder, so a resume does not download it again. `pipeline.run` removes it and the progress file only after `full.txt` is written.
- Retries (3 attempts, exponential backoff) are in the source, around download + transcribe. Each attempt resumes from the progress file, so a retry does not repeat work. A killed process cannot retry itself; the user runs the same input again.

### Package, not a single script
- Run as `python -m app`. The package folder is `app/`, not `transcription/`, so that it is not confused with the project folder. The earlier entry point `python pipeline.py <url>` is removed.
