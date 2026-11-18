# live/ (package)

## How it works

```
python -m live start
   │  (parent: cli.start)
   ├── spawns: python -m live.recorder <session>      input device ──► system/NNNNNN-<wall>-<rate>x<ch>.pcm (appended per block)
   ├── spawns: python -m live.transcriber <session>   polls system/ ──► transcript.partial.jsonl ──► <name>.txt
   │
   Ctrl+C ─► close recorder stdin ─► recorder writes the blocks left, exits
          ─► parent writes <session>/stopped
          ─► transcriber does the rest of the audio, exits
          ─► finalize: merge run files ─► <name>.flac (checked) ─► remove work files
```

The session folder is the only link between the 2 child processes.

```
out/live/YYYYMMDD-HHMMSS-<label>/          <name> = folder name
  system/000001-20261118T140000.000-48000x2.pcm   run file: sequence, wall time of first sample, rate x channels
  system/000002-...pcm                      next recorder run (after a crash or --session)
  transcript.partial.jsonl                  progress, source of truth for the transcript
  <name>.txt                                transcript, rewritten after each commit
  stopped                                   marker: no more audio will come
  recorder.pid, transcriber.pid             one process per role per session
  <name>.flac                               after finalize
```

| File | Role |
|---|---|
| `cli.py` | `start` (spawn both children, handle Ctrl+C, restart a failed transcriber, then finalize), `finalize`, `devices`. |
| `recorder.py` | Audio callback puts each block on a queue. Main thread gives blocks to `RunWriter`, which appends each one to the run file (raw float32) and flushes it to the OS. One run file per recorder run. Stops when stdin closes. Logs input overflow and 30 s of silence. |
| `session.py` | Folder layout, run file names, `Timeline` (run files end to end on one audio time axis, gaps), pid claims. |
| `transcriber.py` | Poll loop. Each pass: span from the last committed `end`, `read_16k`, backend, `choose` what to commit, append to progress, rewrite `<name>.txt`, print new lines. |
| `progress.py` | Append-only JSONL with fsync per write. Truncates a line cut short by a crash. |
| `audio.py` | `read_16k` (span of the timeline as 16 kHz mono, by seek in the run files), `merge` (all run files into one FLAC in blocks, decode again to check the frame count). |
| `finalize.py` | Mark stopped, run the transcriber in this process until done, merge, clean up. |
| `backends/` | Speech-to-text backends behind one interface. See `backends/ARCHITECTURE.md`. |

### Commit rules (`transcriber.choose`, `plan_span`)

- A span starts at the last committed `end` and is at most 30 s. It ends early at the next recording gap.
- Normal pass: commit all segments except the last. The last one is also committed if it ends 1.5 s or more before the span end (speech stopped), or if the span is capped at 30 s and it is the only segment.
- No segment at all: mark the audio done up to span end - 2 s, with a record that has text `""`.
- Final pass (a stopped session reaches its end, or the span ends at a gap): commit all and mark done to the span end.
- A pass runs only when the timeline grew 1 s or more since the last pass, or the backlog is more than 30 s, or the session is stopped. The folder is polled each 0.2 s.

### Time

- Transcript times are audio time: the sum of run file lengths. A gap between recorder runs closes up on this axis, and shows as a `(recording gap HH:MM:SS)` line at that point.
- Wall time of a run file = arrival time of its first block - the block length. A gap is where the next run file starts later than the end of the one before.

## Decisions

### Disk between recorder and transcriber
- Audio goes to disk before transcription, so a transcriber crash, a slow model or a backlog never loses audio. A crash of the recorder loses only the blocks in its queue that are not yet in the run file (≤ 0.2 s). Checked end to end with `kill -9` on the recorder.
- Native rate and channels (BlackHole: 48 kHz stereo). Resampling to 16 kHz mono happens only in memory for the backend (`soxr`).
- Consecutive run files with the same rate are resampled together in `read_16k`, so there is no filter edge at the join.

### Two processes, linked only by the folder
- A crash or hang in Whisper does not stop the recording. The parent restarts a failed transcriber up to 3 times; it resumes from the progress file.
- Children start in their own process group, so Ctrl+C reaches only the parent. The parent controls the stop order: recorder first (so the last blocks are on disk), then the `stopped` marker, then the transcriber finishes.
- A child stops when its stdin closes. If the parent dies, the pipe closes, so no orphan recorder keeps recording. The stdin thread reads the raw file descriptor: a daemon thread that blocks in `sys.stdin.buffer` holds a lock and makes Python abort at exit.
- The parent sets its own SIGINT handler (and treats SIGTERM as Ctrl+C). A process started in the background by a shell can inherit SIGINT as ignored.
- `stopped` is written by the parent after the recorder exits, and the transcriber reads it before it lists the run files. Thus when it sees `stopped`, the listing has all audio.
- Pid files stop a second recorder or transcriber on the same session. A pid file of a dead process is ignored.

### Raw PCM run files, no FLAC snippets
- The first version wrote 5 s FLAC snippets. The transcriber saw audio only after a snippet closed, so each line waited up to 5 s more.
- Raw PCM has no header, so the transcriber can read it while the recorder appends to it. The recorder flushes each block to the OS, and the reader counts only complete frames (file size // frame size). Thus a read gets audio up to about 0.2 s old.
- A read is 1 seek and 1 read of the span (at most 30 s), so its cost does not grow with the file.
- An intermediate version kept FLAC snippets and added a `.pcm` for the open snippet. The change from `.pcm` to FLAC needed: "prefer the FLAC if both exist", "only the next sequence", a read that falls back to the FLAC, a retry when Windows refuses to remove an open file, and a seal step for a `.pcm` left by a crash. One run file per recorder run needs none of these: the recorder never removes or renames a file while it runs.
- A pipe from recorder to transcriber was rejected. Audio in the pipe is lost when the transcriber restarts; the join of disk and pipe needs frame numbers; a full pipe (64 KB on macOS, about 0.2 s at 48 kHz stereo) blocks the recorder and loses audio. The run file has none of these, and the folder stays the only link.
- Float32 as from the audio callback, so the recorder does no conversion per block. The rate and channels are in the name, because the file has no header.
- Cost: disk during the recording is about 1.4 GB per hour at 48 kHz stereo (FLAC 24-bit: about 0.45 GB per hour). Raw PCM has no checksum; the merge still decodes the result again and checks the frame count.
- One file per run, not one per fixed length: a new file is needed only where the wall time can jump (a new run). No file size limit applies on APFS or NTFS.
- End-to-end test (2 min 36 s of speech played into BlackHole, `base` model), delay from the end of a spoken line to its print: 5 s FLAC snippets: median 6.3 s, p90 22.8 s, max 37.0 s. Raw PCM: median 3.6 s, p90 7.2 s, max 12.3 s. Most of the delay that is left comes from the commit rule (the last segment waits for the next one or for 1.5 s of silence).

### Polling, not push
- The transcriber must scan the folder at start and after a crash anyway. A notification would be a second way to find the same files, and it is lost when the transcriber is down. Polling every 0.2 s adds at most 0.2 s; one Whisper pass takes about 0.5 s.
- A live pass waits for 1 s of new audio. Each pass transcribes again all audio after the last commit, so a pass for each poll uses CPU for little new text.

### No state file
- The resume point is the last `end` in `transcript.partial.jsonl`. The run file and offset for it come from the run file sizes.
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
- The merge writes `<name>.flac.tmp` (FLAC 24-bit), decodes it again and compares the frame count, then renames it. Run files are removed only after that. If the check fails, all work files stay.
- The merge reads and resamples in blocks of 1 Mi frames (`soxr.ResampleStream` when a run has another rate), so memory does not grow with the length. A 3 h run file is about 4 GB.
- If `<name>.flac` exists, it was checked before, so `finalize` only cleans up. It does not transcribe again: after a crash during clean-up, the progress file may already be gone.
- A finalized session cannot be continued with `--session`.

### Reuse of app
- From `app`: `models` (`Line`, `timestamp`, `clean`, `safe_name`, `TARGET_LANGUAGE`), `stages.common.transcribe` (`load_model`, `complete_length`, `is_segment`) and `stages.common.write.write_full`. `app.stages.common.transcribe.transcribe` itself is not used: it takes one complete file, and here the audio grows during the run.
