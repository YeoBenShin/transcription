# transcription

Turns a media input into timestamped transcript `.txt` files, split into fixed-length chunks.

## Run

Needs Python 3.11-3.14. `onnxruntime` has no wheels for 3.15.

```sh
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app <input> [--chunk-minutes 4] [--outdir out] [--model small]
```

Supported `<input>`:

| Input | Example | What happens |
|---|---|---|
| Full YouTube video URL, copied from the address bar | `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`, `https://www.youtube.com/shorts/TD3_Na5fmIw` | YouTube captions. If there are none: download the audio, then transcribe with Whisper |
| Local audio/video file | `~/calls/2026-11-02.m4a` | Transcribe with Whisper |

Put quotes around a URL that has `?` or `&`, for example `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`. Without quotes, zsh reads `?` as a glob and stops with `no matches found` before the program starts.

Quote URLs in zsh (`?` and `&` are glob characters).

`--model` is the faster-whisper size (`tiny`, `base`, `small`, `medium`, `large-v3`, ...). It applies only when Whisper runs.

## Output

`<outdir>/<source_id>/` contains:
- `chunk_NNN_HHMMSS-HHMMSS.txt`: one file per time window, with a header line
- `full.txt`: all lines

`source_id` is the YouTube video id or the file name without its extension. A new run removes old chunk files from that folder.

## Moving parts

- `app/cli.py`: command line. Calls `pipeline.run`.
- `app/pipeline.py`: picks a source for the input, then runs chunk + write.
- `app/sources/`: one module per input type. Each one decides which stages to run.
- `app/stages/`: single-purpose steps (captions, download, transcribe, chunk, write).

See `ARCHITECTURE.md` in each folder.
