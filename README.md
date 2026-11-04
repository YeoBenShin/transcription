# transcription

Turns a media input into timestamped transcript `.txt` files, split into fixed-length chunks.

## Run

Needs Python 3.11-3.14. `onnxruntime` has no wheels for 3.15.

```sh
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app <input> [--chunk-minutes 4] [--outdir out] [--model small]
```

### Windows setup

From the project folder, in PowerShell:

```powershell
winget configure -f .config/configuration.winget
.venv\Scripts\python -m app <input>
```

- Installs Python 3.13, Visual C++ x64 runtime, Git, Deno. Then creates `.venv` and installs `requirements.txt`.
- Needs Windows 10 1809+ and winget 1.6+. Accept the UAC prompts.
- Run it again any time. Installed parts are skipped.
- Not tested on Windows.

Supported `<input>`:

| Input | Example | What happens |
|---|---|---|
| Full YouTube video URL, copied from the address bar | `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`, `https://www.youtube.com/shorts/TD3_Na5fmIw` | YouTube captions. If there are none: download the audio, then transcribe with Whisper |
| Spotify podcast episode URL | `'https://open.spotify.com/episode/22EJxI0zfZIcPiLwfBfhls'` | Find the show's public RSS feed. Use the feed's `podcast:transcript` if it has one. If not: download the feed's mp3, then transcribe with Whisper |
| Local audio/video file | `~/calls/2026-11-02.m4a` | Transcribe with Whisper |

Spotify-only (exclusive) episodes have no public RSS feed. They fail with `no transcript could be produced`.

Put quotes around a URL that has `?` or `&`, for example `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`. Without quotes, zsh reads `?` as a glob and stops with `no matches found` before the program starts.

### Batch: every file in a folder

```sh
bin/transcribe-batch [--inbox to-be-transcribe] [--chunk-minutes 4] [--outdir out] [--model small] [--keep]
```

- Transcribes each audio/video file directly inside `to-be-transcribe/` (created on first run). Hidden files and non-media extensions are skipped.
- After success, the file moves to `to-be-transcribe/done/`. `--keep` leaves it in place. A failed file stays in the inbox, and the run continues with the next file.
- Exit code 1 if any file failed.
- `bin/transcribe-batch` runs `.venv/bin/python -m app.batch` from the project folder, so it works from any current directory.

#### Create `bin/transcribe-batch`

The file is in the repo. To make it again, run this from the project folder:

```sh
mkdir -p bin
cat > bin/transcribe-batch <<'EOF'
#!/bin/sh
# Transcribe every media file in to-be-transcribe/. Extra arguments go to python -m app.batch.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
exec .venv/bin/python -m app.batch "$@"
EOF
chmod +x bin/transcribe-batch
```

- It is a shell script, not a compiled binary. The `.venv` must exist (see setup above).
- `chmod +x` makes it executable. Without it, the shell shows `permission denied`.
- Optional, to run it from anywhere as `transcribe-batch`: `ln -s "$PWD/bin/transcribe-batch" /usr/local/bin/transcribe-batch`.

#### Windows

The `sh` script above does not run in Command Prompt or PowerShell. Create `bin\transcribe-batch.cmd` with:

```bat
@echo off
cd /d "%~dp0.." || exit /b 1
.venv\Scripts\python.exe -m app.batch %*
```

- Run it as `bin\transcribe-batch` (no `chmod` needed).
- On Windows the venv puts Python in `.venv\Scripts\`, not `.venv/bin/`.
- Without the wrapper: `.venv\Scripts\python -m app.batch`.
- Not tested on Windows.

### Web interface (local)

```sh
.venv/bin/python -m app.web [--port 8000] [--outdir out]
```

- Open `http://127.0.0.1:8000`. Paste a YouTube or Spotify episode URL, set chunk minutes and Whisper model, click Transcribe.
- Each submit runs `python -m app <url> ...` as a subprocess. Output files are the same as the CLI, in the same `--outdir`.
- Jobs run one at a time. Others wait as `queued`. The page shows the live CLI log and links to view or download each `.txt`.
- Job list is in memory only. A server restart clears the list, not the files.
- Listens on `127.0.0.1` only. No authentication.

`--model` is the faster-whisper size (`tiny`, `base`, `small`, `medium`, `large-v3`, ...). It applies only when Whisper runs.

## Output

`<outdir>/<name>/` contains:
- `chunk_NNN_HHMMSS-HHMMSS.txt`: one file per time window, with a header line
- `full.txt`: all lines

`name` is `<title> [<id>]` for YouTube and Spotify (for example `Creating a C HTTP Server [JT9JI_SMHk4]`), or the file name without its extension for a local file. Characters not allowed in file names are removed from the title, and the title is cut to 120 characters. If the title cannot be fetched, `name` is only the video or episode id. A new run removes old chunk files from that folder.

## Moving parts

- `app/cli.py`: command line for one input. Calls `pipeline.run`.
- `app/batch.py`: command line for a folder. Calls `pipeline.run` once per file.
- `app/web/`: local web page. `backend/` runs `python -m app` per URL. `frontend/` is static HTML, CSS, JS.
- `bin/transcribe-batch`: executable wrapper for `app.batch`.
- `app/pipeline.py`: picks a source for the input, then runs chunk + write.
- `app/sources/`: one module per input type. Each one decides which stages to run.
- `app/stages/`: single-purpose steps. `youtube/` (title, captions, download) and `spotify/` (Spotify metadata, RSS feed lookup, feed transcript) hold stages for one source; `common/` holds stages for more than one (http, transcribe, chunk, write).

See `ARCHITECTURE.md` in each folder.
