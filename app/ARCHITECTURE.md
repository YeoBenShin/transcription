# app/ (package)

## How it works

| File | Role |
|---|---|
| `__main__.py` | Lets you run `python -m app`. Calls `cli.main`. |
| `cli.py` | Parses arguments into `Options`, calls `pipeline.run`, prints a summary. Exit code 1 if the input is not supported or no transcript was produced. |
| `batch.py` | `python -m app.batch`. Lists media files in the inbox folder, calls `pipeline.run` per file, moves each success into `<inbox>/done/`. Exit code 1 if any file failed. |
| `web/` | Local web interface. See `app/web/ARCHITECTURE.md`. |
| `pipeline.py` | `route()` selects a source. `run()` gets the transcript, then chunks and writes it. Returns a `Result`. |
| `models.py` | `Line`, `Transcript`, `Options`, and the `timestamp` / `clean` helpers. |

`pipeline.run` is also the library API (`from app import run, Options`).

## Decisions

- `route()` uses the first match. Thus the order in `SOURCES` is important when two sources can match the same string.
- `pipeline.run` raises exceptions (`UnsupportedInput`, `NoTranscript`). It does not call `sys.exit`. Thus other code can call it, and only the CLI converts errors to exit codes.
- The output folder is `Transcript.folder_name`: the sanitized title, else `source_id`. A title is easier to find in `out/` than an id. Sanitizing removes characters that are not allowed in file names on Windows, so the same output works on all platforms. Two inputs with the same title write to the same folder; the second run replaces the first.
- `Transcript.origin` records how the lines were made (captions or Whisper). Captions and Whisper output have different quality, so later analysis must be able to tell them apart.
- `batch.py` is a separate entry point, not a flag on `cli.py`. A folder of files and one input string have different arguments and output, and `pipeline.run` stays unchanged.
- `batch.py` catches every exception per file. One corrupt file must not stop the rest of the batch.
- Processed files move into `<inbox>/done/`. Thus a second run does only the new files, and the original audio is kept.
- `batch.py` selects files by extension (`MEDIA_SUFFIXES`). `.DS_Store`, notes and other non-media files in the inbox are not sent to Whisper.
