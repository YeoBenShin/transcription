# app/ (package)

## How it works

| File | Role |
|---|---|
| `__main__.py` | Lets you run `python -m app`. Calls `cli.main`. |
| `cli.py` | Parses arguments into `Options`, calls `pipeline.run`, prints a summary. Exit code 1 if the input is not supported or no transcript was produced. |
| `pipeline.py` | `route()` selects a source. `run()` gets the transcript, then chunks and writes it. Returns a `Result`. |
| `models.py` | `Line`, `Transcript`, `Options`, and the `timestamp` / `clean` helpers. |

`pipeline.run` is also the library API (`from app import run, Options`).

## Decisions

- `route()` uses the first match. Thus the order in `SOURCES` is important when two sources can match the same string.
- `pipeline.run` raises exceptions (`UnsupportedInput`, `NoTranscript`). It does not call `sys.exit`. Thus other code can call it, and only the CLI converts errors to exit codes.
- `Transcript.origin` records how the lines were made (captions or Whisper). Captions and Whisper output have different quality, so later analysis must be able to tell them apart.
