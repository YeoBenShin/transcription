# live/backends/

## How it works

- `base.Backend`: `name`, `model`, `transcribe(audio, prompt) -> List[Segment]`. `audio` is 16 kHz mono float32. `Segment` times are seconds from the start of `audio`.
- `make(name, model)` returns a backend. `names()` lists the known names for the CLI.
- `faster_whisper.FasterWhisper`: `app.stages.common.transcribe.load_model` (CPU, int8, cached per process), English, `vad_filter=True`.

To add a backend: a module with a class that has `name`, `model` and `transcribe`, then add it to `make` and `names`. Options and code samples per model are in `app/stages/common/ARCHITECTURE.md` ("Model options").

## Decisions

- The backend gets an array, not a file path. The transcriber already holds the span in memory, and every option in the model table accepts an array or can take one.
- The model loads in `transcribe`, not in `make`. Thus `finalize` with nothing left to transcribe does not load the model.
- The backend name and model are in the progress file header. `finalize` uses them when no `--backend` / `--model` is given. A different model on a continued session is allowed (the transcript is a draft); a note is printed.
