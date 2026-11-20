# live/backends/

## How it works

- `base.Backend`: `name`, `model`, `transcribe(audio, prompt) -> List[Segment]`. `audio` is 16 kHz mono float32. Each `Segment` has its `Word`s. All times are seconds from the start of `audio`.
- `make(name, model)` returns a backend. `names()` lists the known names for the CLI.
- `faster_whisper.FasterWhisper`: `app.stages.common.transcribe.load_model` (CPU, int8, cached per process), English, `vad_filter=True`, `word_timestamps=True`, temperature fallback as faster-whisper's default (0.0, 0.2, … 1.0; a decode with zlib compression ratio > 2.4 or mean log probability < -1.0 is done again at the next temperature), at most 10 tokens per second of audio + 20, and never more than 221 (448 - 227).

To add a backend: a module with a class that has `name`, `model` and `transcribe`, then add it to `make` and `names`. Options and code samples per model are in `app/stages/common/ARCHITECTURE.md` ("Model options").

## Decisions

- The backend gets an array, not a file path. The transcriber already holds the span in memory, and every option in the model table accepts an array or can take one.
- The model loads in `transcribe`, not in `make`. Thus `finalize` with nothing left to transcribe does not load the model.
- Word times are needed by the commit rule (`live/ARCHITECTURE.md`, "Commit rules").
- At the cut end of a span, Whisper sometimes loops ("tell tell tell …", "______") until its 224-token limit. Such a pass took 3-8 s, not about 0.5 s, and all passes after it waited. The temperature fallback was not the cause: with temperature 0 only, these passes still took about 5 s. The token cap made the longest pass about 2-3 s. Normal speech uses about 4-6 tokens per second. The looped words are in the tail, so they are not written.
- Temperature fallback is on. With temperature 0 only, a loop at the cut end of a span ("I mean, I mean, …") was given by pass after pass, agreed, and written: 30 s of the 2x speed benchmark fixture was one loop (WER 0.377). With the fallback, the loop cut in `live/loops.py` and the prompt filter, the WER is 0.15-0.16. Cost: a fallback pass takes 2-5 s, not about 0.9 s (benchmark: pass time p90 1.6-2.2 s, max 3.4-5.2 s). A hot decode (temperature 0.6 and more) can give junk text such as "ㄴㄴㄴ"; it is in the tail and is written only if the next pass gives the same text or the VAD finds a pause.
- The backend name and model are in the progress file header. `finalize` uses them when no `--backend` / `--model` is given. A different model on a continued session is allowed (the transcript is a draft); a note is printed.
- The token cap has an upper limit of 221. Whisper takes at most 448 tokens in one window (prompt + output), and faster-whisper makes a prompt of up to 227 tokens (223 text + 4 control). Without the upper limit, a span over about 20 s with a full prompt failed with `ValueError: ... exceeds the max_length of the Whisper model: 448`. A smaller span or fewer prompt lines was not the fix: 5 long lines can still fill the 223-token prompt, and a 30 s span of fast speech uses about 120-160 tokens, well under 221.
