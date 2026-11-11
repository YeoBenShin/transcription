# transcription

Turns a media input into a timestamped transcript `.txt` file.

There are 2 input types:

1. **Online media**: YouTube videos and Spotify podcast episodes, by URL.
2. **Personal audio**: your own audio/video files, in a folder.

There are 2 ways to run it:

1. **CLI**: `python -m app` (one input) and `python -m app.batch` (a folder of personal audio).
2. **Wrapper scripts**: double-click to start. One opens the web UI (online media). One transcribes the personal audio folder.

## Setup

Needs Python 3.11-3.14. `onnxruntime` has no wheels for 3.15.

```sh
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

### Windows setup

From the project folder, in PowerShell:

```powershell
winget configure -f .config/configuration.winget
```

- Installs Python 3.13, Visual C++ x64 runtime, Git, Deno. Then creates `.venv` and installs `requirements.txt`.
- Needs Windows 10 1809+ and winget 1.6+. Accept the UAC prompts.
- Run it again any time. Installed parts are skipped.
- On Windows, Python is in `.venv\Scripts\`, not `.venv/bin/`. Replace `.venv/bin/python` with `.venv\Scripts\python` in all commands below.

#### Manual venv setup (Windows)

Use this instead of `winget configure` when Python 3.13 is already installed, or to reinstall the packages. Run from the project folder.

1. Create the venv (skip if `.venv` exists):

   ```powershell
   python -m venv .venv
   ```

2. Activate it:

   | Shell | Command |
   |---|---|
   | PowerShell | `.venv\Scripts\Activate.ps1` |
   | Command Prompt | `.venv\Scripts\activate.bat` |

3. Install the requirements:

   ```powershell
   python -m pip install -r requirements.txt
   ```

- After activation, the prompt starts with `(.venv)`, and `python` means `.venv\Scripts\python.exe`.
- If PowerShell says `running scripts is disabled on this system`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.
- `deactivate` leaves the venv.
- Activation is optional. The wrapper scripts and the `.venv\Scripts\python` commands below use the venv without it.

## Tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

- `tests/test_output_files.py`: output file name, header line, name conflicts (`<name>-<id>.txt`), and `temp/` cleanup. Uses a fake source through `pipeline.run`. No network, no Whisper.

## Method 1: CLI

### One input: `python -m app`

```sh
.venv/bin/python -m app <input> [--outdir out] [--model base]
```

Supported `<input>`:

| Input | Example | What happens |
|---|---|---|
| Full YouTube video URL, copied from the address bar | `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`, `https://www.youtube.com/shorts/TD3_Na5fmIw` | YouTube captions. If there are none: download the audio, then transcribe with Whisper |
| Spotify podcast episode URL | `'https://open.spotify.com/episode/22EJxI0zfZIcPiLwfBfhls'` | Find the show's public RSS feed. Use the feed's `podcast:transcript` if it has one. If not: download the feed's mp3, then transcribe with Whisper |
| Local audio/video file | `2026-11-02.m4a` | Transcribe with Whisper |

#### Points to Note:
- Spotify-only (exclusive) episodes have no public RSS feed. They fail with `no transcript could be produced`.
- Put quotes around a URL that has `?` or `&`, for example `'https://www.youtube.com/watch?v=JT9JI_SMHk4'`. Without quotes, zsh reads `?` as a glob and stops with `no matches found` before the program starts.

### Personal audio folder: `python -m app.batch`

```sh
.venv/bin/python -m app.batch [--inbox to-be-transcribe] [--outdir out] [--model base] [--keep]
```

- Transcribes each audio/video file directly inside `to-be-transcribe/` (created on first run). Hidden files and non-media extensions are skipped.
- After success, the file moves to `to-be-transcribe/done/`. `--keep` leaves it in place. A failed file stays in the inbox, and the run continues with the next file.
- Exit code 1 if any file failed.

`--model` is the faster-whisper size (`tiny`, `base`, `small`, `medium`, `large-v3`, ...). It applies only when Whisper runs.

## Output

The transcript is `<outdir>/<name>.txt`. There is no folder per transcript.

`<name>` is `YYYYMMDD-Author-Title`:

| Input | YYYYMMDD | Author | Title |
|---|---|---|---|
| YouTube | upload date | channel | video title |
| Spotify | release date | show name | episode title |
| Local file | `date` or `creation_time` tag of the file, else the file creation date (modification date if the OS has no creation date) | `artist`, `album_artist`, `author` or `composer` tag, else `Unknown` | file name without extension |

- No date gives `00000000`. No author gives `Unknown`. No title gives the video or episode id.
- Characters not allowed in file names are removed. The author is cut to 60 characters, the title to 120.
- Example: `20261031-mira-Creating a C HTTP Server to query a Minecraft Chest.txt`.

The first line of the file is `<name>-<id>`, then a blank line, then the transcript. `<id>` is the YouTube video id, the Spotify episode id, or the local file name without extension.

If `<name>.txt` already exists with a different first line (another input with the same date, author and title), the new transcript is written as `<name>-<id>.txt`. The existing file is not changed. A run of the same input again replaces its own file.

While Whisper runs, `<outdir>/temp/` contains:
- `<name>.partial.jsonl`: each line Whisper has made so far.
- `<name>.<ext>`: the downloaded audio (YouTube and Spotify only).

Both are removed after `<name>.txt` is written. `temp/` is removed when it is empty.

### Failures and resume

- A Whisper failure is retried 2 times, after 2 s and 4 s. After 3 failed attempts the run stops with `failed after 3 attempts: ...` and exit code 1. Ctrl+C stops at once, with no retry.
- To resume, run the same input again (same URL, or the same file in the inbox). If `temp/<name>.partial.jsonl` exists, Whisper continues from the last saved line. If `temp/<name>.<ext>` exists, the audio is not downloaded again.
- A resume needs the same `<name>` and the same `--model` (in the header line of the `.partial.jsonl`). The audio file must also be the same. If one is different, Whisper starts again from the beginning.
- If the date, author or title cannot be fetched on one run, `<name>` is different, so that run does not find the earlier progress.

## Method 2: Wrapper scripts

Double-click the script in Finder (macOS) or File Explorer (Windows). It also runs from a terminal, from any current directory. Extra arguments go to the Python command.

| Script | Runs | Use for |
|---|---|---|
| `bin/transcribe-web` (`.cmd` on Windows) | `python -m app.web`, then opens the browser | Online media (YouTube, Spotify) |
| `bin/transcribe-batch` (`.cmd` on Windows) | `python -m app.batch` | Personal audio in `to-be-transcribe/` |

- The `.venv` must exist (see [Setup](#setup)).
- To create the scripts, see [Create the wrapper scripts](#create-the-wrapper-scripts).

### Web UI

```sh
.venv/bin/python -m app.web [--port 8000] [--outdir out]
```

- Open `http://127.0.0.1:8000`. Paste a YouTube or Spotify episode URL, set Whisper model, click Transcribe.
- Each submit runs `python -m app <url> ...` as a subprocess. Output files are the same as the CLI, in the same `--outdir`.
- Jobs run one at a time. Others wait as `queued`. The page shows the live CLI log and links to view or download each `.txt`.
- Job list is in memory only. A server restart clears the list, not the files.
- Listens on `127.0.0.1` only. No authentication.
- Stop the server with Ctrl+C, or close the terminal window.

## Create the wrapper scripts

Run from the project folder.

### macOS / Linux

```sh
mkdir -p bin

cat > bin/transcribe-web <<'EOF'
#!/bin/sh
# Start the local web UI and open it in the browser. Extra arguments go to python -m app.web.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
(sleep 2; open http://127.0.0.1:8000 2>/dev/null || xdg-open http://127.0.0.1:8000) &
exec .venv/bin/python -m app.web "$@"
EOF

cat > bin/transcribe-batch <<'EOF'
#!/bin/sh
# Transcribe every media file in to-be-transcribe/. Extra arguments go to python -m app.batch.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
exec .venv/bin/python -m app.batch "$@"
EOF

chmod +x bin/transcribe-web bin/transcribe-batch
```

- They are shell scripts, not compiled binaries.
- `chmod +x` makes them executable. Without it, the shell shows `permission denied`, and Finder opens the file in a text editor.
- If you start `transcribe-web` with `--port`, the browser still opens port 8000. Open the correct port yourself.
- Optional, to run from anywhere by name: `ln -s "$PWD/bin/transcribe-batch" /usr/local/bin/transcribe-batch` (same for `transcribe-web`).

### Windows

The `sh` scripts above do not run in Command Prompt or PowerShell. Create these 2 files.

Save the content below into the files.

`bin\transcribe-web.cmd`:

```bat
@echo off
cd /d "%~dp0.." || exit /b 1
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:8000"
.venv\Scripts\python.exe -m app.web %*
```

`bin\transcribe-batch.cmd`:

```bat
@echo off
cd /d "%~dp0.." || exit /b 1
.venv\Scripts\python.exe -m app.batch %*
pause
```

- No `chmod` needed.
- `pause` keeps the window open after a double-click, so you can read the result.

Run them from the project folder:

| Shell | Command |
|---|---|
| Command Prompt | `bin\transcribe-web` |
| PowerShell | `.\bin\transcribe-web.cmd` |
| File Explorer | Double-click `bin\transcribe-web.cmd` |

- Same pattern for `transcribe-batch`. Extra arguments go after the name, for example `.\bin\transcribe-web.cmd --port 8001`.
- PowerShell needs the `.\` prefix. It does not run scripts from the current folder by name alone.

## Moving parts

- `app/cli.py`: command line for one input. Calls `pipeline.run`.
- `app/batch.py`: command line for a folder. Calls `pipeline.run` once per file.
- `app/web/`: local web page. `backend/` runs `python -m app` per URL. `frontend/` is static HTML, CSS, JS.
- `bin/transcribe-web`, `bin/transcribe-batch`: executable wrappers for `app.web` and `app.batch`.
- `app/pipeline.py`: picks a source for the input, then writes `<outdir>/YYYYMMDD-Author-Title.txt`.
- `app/sources/`: one module per input type. Each one decides which stages to run.
- `app/stages/`: single-purpose steps. `youtube/` (date/channel/title, captions, download), `spotify/` (Spotify metadata, RSS feed lookup, feed transcript) and `local/` (file date and author tags) hold stages for one source; `common/` holds stages for more than one (http, transcribe, write; chunk is not used now).

See `ARCHITECTURE.md` in each folder.
