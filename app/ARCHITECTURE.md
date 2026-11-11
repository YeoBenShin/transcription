# app/ (package)

## How it works

| File | Role |
|---|---|
| `__main__.py` | Lets you run `python -m app`. Calls `cli.main`. |
| `cli.py` | Parses arguments into `Options`, calls `pipeline.run`, prints a summary. Exit code 1 if the input is not supported or no transcript was produced. |
| `batch.py` | `python -m app.batch`. Lists media files in the inbox folder, calls `pipeline.run` per file, moves each success into `<inbox>/done/`. Exit code 1 if any file failed. |
| `web/` | Local web interface. See `app/web/ARCHITECTURE.md`. |
| `pipeline.py` | `route()` selects a source. `run()` gets the transcript, writes it as `<outdir>/<name>.txt`, then removes `temp/<name>.partial.jsonl`, `Transcript.downloaded_audio`, and `temp/` if it is empty. Returns a `Result`. |
| `models.py` | `Line`, `Transcript`, `Options` (with `temp_dir`), and the `output_name` / `timestamp` / `clean` / `safe_name` helpers. |

`pipeline.run` is also the library API (`from app import run, Options`).

## Decisions

- `route()` uses the first match. Thus the order in `SOURCES` is important when two sources can match the same string.
- `pipeline.run` raises exceptions (`UnsupportedInput`, `NoTranscript`, `GaveUp`). It does not call `sys.exit`. Thus other code can call it, and only the CLI converts errors to exit codes.
- The output file stem is `Transcript.name`, made by `output_name(date, author, title, source_id)`: `YYYYMMDD-Author-Title`. Missing parts become `00000000`, `Unknown`, or the source id for the title, so the name always has the same 3 parts. Sanitizing removes characters that are not allowed in file names on Windows, so the same output works on all platforms. The author is cut to 60 and the title to 120 characters, so `<name>.partial.jsonl` stays under the 255-character file name limit.
- Each source calls `output_name` before it transcribes, because the Whisper progress and downloaded audio in `temp/` use the same name.
- `Transcript.origin` records how the lines were made (captions or Whisper). Captions and Whisper output have different quality, so later analysis must be able to tell them apart.
- `batch.py` is a separate entry point, not a flag on `cli.py`. A folder of files and one input string have different arguments and output, and `pipeline.run` stays unchanged.
- `batch.py` catches every exception per file. One corrupt file must not stop the rest of the batch.
- Processed files move into `<inbox>/done/`. Thus a second run does only the new files, and the original audio is kept.
- `batch.py` selects files by extension (`MEDIA_SUFFIXES`). `.DS_Store`, notes and other non-media files in the inbox are not sent to Whisper.
