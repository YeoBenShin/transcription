# app/web/ (local web interface)

## How it works

```
browser ── frontend/ (index.html, style.css, app.js)
   │  POST /api/jobs {url, model}
   │  GET  /api/jobs                (poll)
   │  GET  /files/<folder>/<name>.txt[?download]
   ▼
backend/server.py ──► backend/runner.py ──► subprocess: python -u -m app <url> --outdir .. --model ..
                                               │
                                               ▼
                                         <outdir>/<folder>/*.txt
```

| File | Role |
|---|---|
| `__main__.py` | Lets you run `python -m app.web`. Calls `backend.server.main`. |
| `backend/server.py` | Stdlib HTTP server on `127.0.0.1`. Serves `frontend/index.html` at `/`, other frontend files at `/static/<name>`, the jobs API at `/api/jobs`, and `.txt` files from `--outdir` at `/files/`. Validates the POST body. |
| `backend/runner.py` | `Job` and `Runner`. One worker thread takes queued jobs, runs the CLI as a subprocess, and stores its merged stdout/stderr. When the subprocess ends, the `.txt` paths the CLI printed become the job's files. Status: `queued`, `running`, `done`, `failed`. Each job reports `elapsed` seconds: from `running` until `done`/`failed`, none while `queued`. |
| `frontend/index.html` | Page markup: URL form, options, job list container. |
| `frontend/style.css` | Styles. Light and dark through `prefers-color-scheme`. |
| `frontend/app.js` | Submits the form, polls `/api/jobs` (1 s while a job is active, 4 s otherwise), renders log, file links and each job's running time. A log at the bottom follows new output; a log scrolled up keeps its position across polls. |

## Decisions

### Backend and frontend in separate folders
- `frontend/` has only static files. It talks to the backend only through the HTTP API, so either side can change without editing the other.
- The backend serves only `.html`, `.css` and `.js` from `frontend/` (`STATIC_TYPES`). Paths outside `frontend/` return 404.

### Run the CLI as a subprocess
- The runner calls `python -m app`, not `pipeline.run` in-process. Thus output files, log text and exit codes are exactly the same as the CLI, and a crash or the Whisper model memory stays in the child process.
- Result files come from the paths the CLI prints. Thus the web interface does not need to know how the CLI names output folders.

### One job at a time
- Two Whisper runs in parallel compete for CPU and RAM, and are not faster in total.

### Standard library only
- No new dependency for a local single-user page. No build step for the frontend.

### Input and file limits
- Accepts only `http(s)` URLs. Local paths go through the CLI or batch.
- `/files/` serves only `.txt` files that resolve inside `--outdir`. Paths with `..` return 404.
- The job list is in memory only. A restart clears the list, not the files.
