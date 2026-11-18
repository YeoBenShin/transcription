# live/ (package)

## How it works

```
python -m live start
   │  (parent: cli.start)
   ├── spawns: python -m live.recorder <session>      input device ──► system/NNNNNN-<wall>.flac
   ├── spawns: python -m live.transcriber <session>   polls system/ ──► transcript.partial.jsonl ──► <name>.txt
   │
   Ctrl+C ─► close recorder stdin ─► recorder writes last snippet, exits
          ─► parent writes <session>/stopped
          ─► transcriber does the rest of the audio, exits
          ─► finalize: merge snippets ─► <name>.flac (checked) ─► remove work files
```

The session folder is the only link between the 2 child processes.

```
out/live/YYYYMMDD-HHMMSS-<label>/          <name> = folder name
  system/000001-20261118T140000.000.flac   snippet: sequence, wall time of first sample
  system/000002-...flac.tmp                 snippet being written (never read)
  transcript.partial.jsonl                  progress, source of truth for the transcript
  <name>.txt                                transcript, rewritten after each commit
  stopped                                   marker: no more snippets will come
  recorder.pid, transcriber.pid             one process per role per session
  <name>.flac                               after finalize
```

| File | Role |
|---|---|
| `cli.py` | `start` (spawn both children, handle Ctrl+C, restart a failed transcriber, then finalize), `finalize`, `devices`. |
| `recorder.py` | Audio callback puts each block on a queue. Main thread gives blocks to `SnippetWriter`, which writes one FLAC per `--snippet-seconds` (`.tmp`, then rename). Stops when stdin closes; writes the frames left as a shorter last snippet. Logs input overflow and 30 s of silence. |
| `session.py` | Folder layout, snippet names, `Timeline` (snippets end to end on one audio time axis, gaps), pid claims. |
| `transcriber.py` | Poll loop. Each pass: span from the last committed `end`, `read_16k`, backend, `choose` what to commit, append to progress, rewrite `<name>.txt`, print new lines. |
| `progress.py` | Append-only JSONL with fsync per write. Truncates a line cut short by a crash. |
| `audio.py` | `read_16k` (span of the timeline as 16 kHz mono), `merge` (all snippets into one FLAC, decode again to check the frame count). |
| `finalize.py` | Mark stopped, run the transcriber in this process until done, merge, clean up. |
| `backends/` | Speech-to-text backends behind one interface. See `backends/ARCHITECTURE.md`. |

### Commit rules (`transcriber.choose`, `plan_span`)

- A span starts at the last committed `end` and is at most 30 s. It ends early at the next recording gap.
- Normal pass: commit all segments except the last. The last one is also committed if it ends 1.5 s or more before the span end (speech stopped), or if the span is capped at 30 s and it is the only segment.
- No segment at all: mark the audio done up to span end - 2 s, with a record that has text `""`.
- Final pass (a stopped session reaches its end, or the span ends at a gap): commit all and mark done to the span end.
- A pass runs only when a new snippet arrived, or the backlog is more than 30 s, or the session is stopped.

### Time

- Transcript times are audio time: the sum of snippet lengths. A gap between recorder runs closes up on this axis, and shows as a `(recording gap HH:MM:SS)` line at that point.
- Wall time of a snippet = run start + frames before it / rate. Thus the snippets of one run have no gap between them, and a gap appears only between runs (crash, restart).

## Decisions

### Disk between recorder and transcriber
- Audio goes to disk before transcription, so a transcriber crash, a slow model or a backlog never loses audio. A crash of the recorder loses at most the snippet in memory (≤ 5 s).
- Snippets are written as `<name>.flac.tmp` and renamed. The transcriber lists only names that match the snippet pattern, so it never reads a half-written file.
- Native rate and channels (BlackHole: 48 kHz stereo), FLAC 24-bit. Lossless, about half the size of WAV. Resampling to 16 kHz mono happens only in memory for the backend (`soxr`).
- Consecutive snippets with the same rate are resampled together in `read_16k`, so there is no filter edge at each join.

### Two processes, linked only by the folder
- A crash or hang in Whisper does not stop the recording. The parent restarts a failed transcriber up to 3 times; it resumes from the progress file.
- Children start in their own process group, so Ctrl+C reaches only the parent. The parent controls the stop order: recorder first (so the last snippet is on disk), then the `stopped` marker, then the transcriber finishes.
- A child stops when its stdin closes. If the parent dies, the pipe closes, so no orphan recorder keeps recording. The stdin thread reads the raw file descriptor: a daemon thread that blocks in `sys.stdin.buffer` holds a lock and makes Python abort at exit.
- The parent sets its own SIGINT handler (and treats SIGTERM as Ctrl+C). A process started in the background by a shell can inherit SIGINT as ignored.
- `stopped` is written by the parent after the recorder exits, and the transcriber reads it before it lists the snippets. Thus when it sees `stopped`, the listing has all snippets.
- Pid files stop a second recorder or transcriber on the same session. A pid file of a dead process is ignored.

### Polling, not push
- The transcriber must scan the folder at start and after a crash anyway. A notification would be a second way to find the same files, and it is lost when the transcriber is down. Polling every 0.5 s adds at most 0.5 s; one Whisper pass takes seconds.

### No state file
- The resume point is the last `end` in `transcript.partial.jsonl`. The snippet and offset for it come from the snippet lengths (FLAC headers, read once per file and cached).
- Silence is written as records with text `""`. Without them, the resume point would stay at the last speech and each pass would grow over all the silence after it.

### Commit rules
- The last segment of a span can be a word cut at the span end, so it waits for more audio. It is committed early only when silence follows it.
- "No speech" is decided only from no segments at all. A span with one uncommitted segment must not move forward: that skipped a full sentence in an end-to-end test.
- A span never crosses a recording gap. Across a gap, Whisper joined the last words of one run to the first words of the next.
- The prompt (last 5 lines) uses only lines from the same recording run. With the text from before a gap as prompt, Whisper repeated the prompt instead of the new speech.

### Output
- `<name>.txt` uses `app.stages.common.write.write_full`, so the format is the same as `python -m app` (header `<name>-live`, `[HH:MM:SS] text`). It is rewritten in full after each commit (atomic `.tmp` + rename), so it is always complete.
- The live transcript is a draft. A better transcript comes from `python -m app` on the merged `.flac`, so there is no second pass here.

### Finalize
- The merge writes `<name>.flac.tmp`, decodes it again and compares the frame count, then renames it. Snippets are removed only after that. If the check fails, all work files stay.
- If `<name>.flac` exists, it was checked before, so `finalize` only cleans up. It does not transcribe again: after a crash during clean-up, the progress file may already be gone.
- A finalized session cannot be continued with `--session`.

### Reuse of app
- From `app`: `models` (`Line`, `timestamp`, `clean`, `safe_name`, `TARGET_LANGUAGE`), `stages.common.transcribe` (`load_model`, `complete_length`, `is_segment`) and `stages.common.write.write_full`. `app.stages.common.transcribe.transcribe` itself is not used: it takes one complete file, and here the audio grows during the run.
