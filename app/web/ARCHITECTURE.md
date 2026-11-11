# app/web/ (local web interface)

## How it works

```
browser ── frontend/ (index.html, style.css, app.js)
   │  POST /api/jobs {url, model}
   │  GET  /api/jobs                (poll)
   │  POST /api/jobs/<id>/stop | continue | delete
   │  GET  /files/<name>.txt[?download]
   ▼
backend/server.py ──► backend/runner.py ──► subprocess: python -u -m app <url> --outdir .. --model ..
                                               │
                                               ▼
                                         <outdir>/<name>.txt
```

| File | Role |
|---|---|
| `__main__.py` | Lets you run `python -m app.web`. Calls `backend.server.main`. |
| `backend/server.py` | Stdlib HTTP server on `127.0.0.1`. Serves `frontend/index.html` at `/`, other frontend files at `/static/<name>`, the jobs API at `/api/jobs` and `/api/jobs/<id>/<action>`, and `.txt` files from `--outdir` at `/files/`. Validates the POST body. |
| `backend/runner.py` | `Job` and `Runner`. One worker thread takes queued jobs, runs the CLI as a subprocess, and stores its merged stdout/stderr. When the subprocess ends, the `.txt` paths the CLI printed become the job's files. Status: `queued`, `running`, `done`, `failed`, `stopped`. Each job reports `elapsed` seconds: from `running` until it ends, none while `queued`. `stop` kills the process group; `resume` (the Continue button) re-queues a stopped or failed job with the same arguments; `delete` removes a stopped or failed job's work files in `temp/` and the job. The job's `temp_name` comes from the `temp name: <name>` line in the CLI log. |
| `frontend/index.html` | Page markup: URL form, options, job list container. |
| `frontend/style.css` | Styles. Light and dark through `prefers-color-scheme`. |
| `frontend/app.js` | Submits the form, polls `/api/jobs` (1 s while a job is active, 4 s otherwise), renders log, file links, each job's running time, the Stop / Continue / Delete buttons, and the temp files of a stopped or failed job. It renders again only when the `/api/jobs` response changed, so a button is not replaced while the user clicks it. A log at the bottom follows new output; a log scrolled up keeps its position across polls. |

## Decisions

### Backend and frontend in separate folders
- `frontend/` has only static files. It talks to the backend only through the HTTP API, so either side can change without editing the other.
- The backend serves only `.html`, `.css` and `.js` from `frontend/` (`STATIC_TYPES`). Paths outside `frontend/` return 404.

### Run the CLI as a subprocess
- The runner calls `python -m app`, not `pipeline.run` in-process. Thus output files, log text and exit codes are exactly the same as the CLI, and a crash or the Whisper model memory stays in the child process.
- Result files come from the paths the CLI prints. Thus the web interface does not need to know how the CLI names output folders.

### Stop, Continue, Delete
- Stop kills the CLI and keeps `temp/`. The progress file is fsynced per line and the next run cuts a half-written last line, so a kill at any point is safe to resume.
- Stop sends SIGKILL to the process group (`start_new_session`), or `taskkill /T /F` on Windows. Thus yt-dlp, ffmpeg and deno children stop too. A clean shutdown is not needed: nothing in the CLI must run before exit.
- Continue reuses the same job (same id, URL, model, log with a `--- continued ---` line). The same model is needed, or the progress file header does not match and Whisper starts again.
- Delete finds files by `<name>`, which only the CLI knows. The CLI prints `temp name: <name>` before it writes to `temp/`. The runner reads only the last 1024 log characters after each read, because the log is cut to its last 200,000 characters and a long run removes the start.
- Delete is refused while a queued or running job has the same URL or `temp_name`, because that job uses the same files.
- Delete removes the job from the list. Without its temp files, the job has nothing left to continue from.
- Failed jobs also get Continue and Delete. A failed run (3 Whisper failures, or a crash) keeps `temp/` like a stopped one, so it can resume or be cleaned up the same way.

### One job at a time
- Two Whisper runs in parallel compete for CPU and RAM, and are not faster in total.

### Standard library only
- No new dependency for a local single-user page. No build step for the frontend.

### Input and file limits
- Accepts only `http(s)` URLs. Local paths go through the CLI or batch.
- `/files/` serves only `.txt` files that resolve inside `--outdir`. Paths with `..` return 404.
- The job list is in memory only. A restart clears the list, not the files.
