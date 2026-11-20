# desktop/ (package)

## How it works

```
python -m desktop  /  Transcription.app, Transcription.exe
   │  entry.main: no arguments -> app.main (window); "-m <module> ..." -> runpy (child process)
   ▼
app.main ── Settings.load ── OnboardingDialog (first run, or no installed model) ── MainWindow
                                                                                   │
   record button ──► jobs.LiveRecording ──► child: <exe> -u -m live start --stdin-stop --outdir <out> --backend .. --model <dir> <sources>
                         ▲   JSON event lines (LIVE_EVENTS=1): session, state, line, draft, log
                         └── stop: close stdin -> live stops recorders, transcribes the rest, finalizes
   drop / Files / Folder / Link / Merge / Finish ──► jobs.JobQueue (one at a time)
                         ──► child: <exe> -u -m app <file|url> | app.batch --inbox <folder> --keep | transcript_merge | live finalize
   list ◄── history.scan(<out>)   (<out>/*.txt and <out>/live/<session>/), refreshed by a QFileSystemWatcher and when a job ends
```

| File | Role |
|---|---|
| `entry.py` | Entry point of the source run and of both packaged executables. `-m <module>` runs a module like `python -m`. |
| `app.py` | `main`: theme, settings, onboarding, main window. |
| `settings.py` | `Settings` (model id, output folder, system audio source and device, microphone, onboarded), JSON in the data folder. |
| `paths.py` | Data folder (models, settings): `~/Library/Application Support/Transcription` on macOS, `%LOCALAPPDATA%\Transcription` on Windows. `TRANSCRIPTION_DATA_DIR` overrides it. Default output folder `~/Documents/Transcription`. |
| `commands.py` | Command line of each child process from the settings. `executable()`: Python from source, the worker executable in the packaged app. `child_env()`: `LIVE_EVENTS=1`, unbuffered UTF-8 output, source root on `PYTHONPATH` from source. |
| `process.py` | `Child`: one child process. Output lines (split at `\n` and `\r`) go to a callback from a reader thread. `close_stdin()` asks it to finish; `kill()` kills it with its children (process group / `taskkill /T`). |
| `jobs.py` | Qt objects over `Child`: `JobQueue` and `Job` (status, short detail from the output: `[HH:MM:SS]` progress, `[n/N] name` of a folder), `LiveRecording` (event lines to signals). |
| `history.py` | `scan`: the transcripts in the output folder, newest first. `search`: text or title contains the query. |
| `devices.py` | Input devices (sounddevice) and, on Windows, loopback playback devices (pyaudiowpatch) for the settings. |
| `permissions.py` | macOS System Audio Recording permission status and request, through the `live/native` helper. |
| `selfcheck.py` | `python -m desktop.selfcheck`: import every engine and audio library, build the window off screen. Used by the packaged build test. |
| `models/` | Model catalog and downloader. See `models/ARCHITECTURE.md`. |
| `ui/` | Widgets. See `ui/ARCHITECTURE.md`. |

## Decisions

### A window over the command line tools
- The window starts the same CLIs as a terminal user (`live start`, `app`, `app.batch`, `transcript_merge`, `live finalize`) as child processes. The files, names, resume and finalize rules stay in one place, and a crash or the model memory stays in the child.
- The web UI (`app/web`) made the same choice for the same reasons (see `app/web/ARCHITECTURE.md`).
- From source the child is `python -m <module>`. In the packaged app there is no Python executable, so the entry point itself takes `-m <module>` and runs it with `runpy`. Thus `live.cli.spawn` (which starts recorders and the transcriber as `sys.executable -m ...`) works with no change in both cases.

### Control of `live start`: stdin and JSON events
- Stop: `live start --stdin-stop` stops the recording when its stdin closes. Ctrl+C does not work for a child on Windows (no console, and `CTRL_C_EVENT` goes to a whole console group), and stdin is already how `live start` stops its own children.
- Output: with `LIVE_EVENTS=1`, `live.console` writes one JSON line per log line, transcript line, draft change, session folder and state change. The variable goes to the grandchildren (recorders, transcriber) through the environment, so all of them write events to the same pipe. The window does not parse terminal text.
- The window shows the draft line as the terminal does: agreed words plain, the tail dimmed.

### One job at a time, live recording beside it
- Two Whisper runs in parallel compete for CPU and are not faster in total (same as the web UI). A live recording is not in the queue: it must start when the user presses the button.
- A folder is one `app.batch --keep` job, as asked: the batch CLI already skips non-media files and goes on after a failed file. `--keep` because moving the user's files into `done/` is not expected from a window.
- Stop kills the job with its child processes. The progress file in `<out>/temp/` stays, so adding the same input again continues from it.

### History from the output folder, no database
- The output folder is the source of truth: a transcript made from the terminal, or moved in by hand, shows in the list. A database would need to be kept in step with the folder.
- Delete moves to the trash (`QFile.moveToTrash`), so a click by mistake can be undone.
- A live session without its merged `.flac` is shown as "not finished" with a Finish action (`live finalize`), for example after a quit during a recording.

### Two executables in the packaged app
- On Windows a windowed (GUI) executable has no console, and the output of its child processes to a pipe is not reliable. The window is `Transcription` (no console); the child processes run `transcription-worker` (console, started with `CREATE_NO_WINDOW`, so no window shows). macOS uses the same layout, so the code has one path.

### Models are downloaded, not shipped
- See `models/ARCHITECTURE.md`.
