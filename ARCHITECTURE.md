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
              ├─ source.get_transcript(raw, options) ──► Transcript(source_id, lines, origin, name)
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
- Each segment is appended to `<outdir>/temp/<name>.partial.jsonl` as it is made. A run of the same input continues from the `end` time of the last saved line. Line text is not used to find the resume point: Whisper gives different text and segment boundaries when it starts at a different point, so text matching is not reliable.
- The audio is cut exactly at the last `end`, with no overlap. An overlap gives duplicate lines, and a segment that crosses the cut point cannot be split without word timestamps. The last 5 lines go to Whisper as `initial_prompt` for context.
- The progress file is found by its file name: `<outdir>/temp/<name>.partial.jsonl`, with the same `<name>` as the transcript. Its header line (model, source id, audio size, audio sample count) must then be equal, or the progress is discarded. The model must match because a different model gives different text and segment times. For a local file the source id is only the file name, so the audio size and length are what detect a different file with the same name.
- Downloaded audio is kept as `<outdir>/temp/<name>.<ext>`, so a resume finds it by name and does not download it again. `pipeline.run` removes it and the progress file only after `<name>.txt` is written.

### Flat output, one temp folder
- Each transcript is one file, `<outdir>/YYYYMMDD-Author-Title.txt`. A folder per transcript held only one file, so it added a click and no information. The date first makes the files sort by date. The author shows the channel, show or speaker without opening the file.
- Work files (progress and downloaded audio) go in one `<outdir>/temp/` folder, not next to the transcripts. Thus `<outdir>/` has only finished transcripts.
- Work files use the same `<name>` as the transcript. The name is the key to find earlier progress, so no separate index file is necessary.
- The source id is not in the name, so the usual name stays short and readable. The first line of each `.txt` is `<name>-<source_id>`. Before a write, `write_full` reads the first line of `<name>.txt`: if it is equal, the same input is written again and the file is replaced; if not, another input already has that name, and the new transcript goes to `<name>-<source_id>.txt`. Thus a name conflict never deletes a transcript, and the first input keeps the short name.
- Two inputs with the same name share one progress file name in `temp/`. The header check (source id, audio size and length) discards the progress of the other input, so the lines never mix. Only the work of an unfinished run can be lost.
- A local file has its file name as the source id. Two local files with the same name, date and author have the same header, so the second one replaces the first.
- Retries (3 attempts, exponential backoff) are in the source, around download + transcribe. Each attempt resumes from the progress file, so a retry does not repeat work. A killed process cannot retry itself; the user runs the same input again.

### Package, not a single script
- Run as `python -m app`. The package folder is `app/`, not `transcription/`, so that it is not confused with the project folder. The earlier entry point `python pipeline.py <url>` is removed.
