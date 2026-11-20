# live/ (package)

## How it works

```
python -m live start [--tap | --loopback | --device NAME] [--mic | --mic-only] [--stdin-stop]
   │  (parent: cli.start)
   ├── spawns: python -m live.recorder <session> --stream system   system audio (not with --mic-only) ──► system/NNNNNN-<wall>-<rate>x<ch>.pcm (appended per block)
   ├── spawns: python -m live.recorder <session> --stream mic      (with --mic or --mic-only) microphone ──► mic/NNNNNN-...pcm
   ├── spawns: python -m live.transcriber <session>                polls each stream folder ──► transcript.<stream>.partial.jsonl ──► <name>.txt
   │
   Ctrl+C (or stdin closes, with --stdin-stop) ─► close the stdin of each recorder ─► each writes the blocks left, exits
          ─► parent writes <session>/stopped
          ─► transcriber does the rest of the audio of all streams, exits
          ─► finalize: merge run files per stream ─► FLAC file(s) (checked) ─► remove work files
```

The session folder is the only link between the child processes.

```
out/live/YYYYMMDD-HHMMSS-<label>/          <name> = folder name
  system/000001-20261118T140000.000-48000x2.pcm   run file: sequence, wall time of first sample, rate x channels
  system/000002-...pcm                      next recorder run (after a recorder restart or --session)
  mic/000001-...pcm                         with --mic: run files of the microphone
  transcript.system.partial.jsonl           progress per stream, source of truth for the transcript
  transcript.mic.partial.jsonl
  <name>.txt                                transcript of all streams, rewritten after each commit
  stopped                                   marker: no more audio will come
  recorder.system.pid, recorder.mic.pid, transcriber.pid   one process per role per session
  <name>.flac                               after finalize: the system stream, or with 2 streams the mix
  <name>.system.flac, <name>.mic.flac       after finalize, with 2 streams only: each stream alone, same length as the mix
```

| File | Role |
|---|---|
| `cli.py` | `start` (spawn one recorder per stream and the transcriber, restart a child that exits, handle Ctrl+C, then finalize), `finalize`, `devices`. `Child`: one child process and its restart rules, the same for recorders and the transcriber. |
| `recorder.py` | One stream (`--stream system` or `mic`). Source: an input device (sounddevice, `--device`; for `mic` the default input device if no `--device`), with `--loopback` the WASAPI loopback of a Windows playback device (pyaudiowpatch), or with `--tap` what all apps play on macOS 14.4+ (the `native/` helper as a child process: one JSON header line, then raw float32 frames on its stdout). Audio callback puts each block on a queue. Main thread gives blocks to `RunWriter`, which appends each one to the run file (raw float32) and flushes it to the OS. One run file per recorder run. With loopback and tap, `RunWriter` fills the time with no blocks with zeros (see Time). Stops when stdin closes. Logs input overflow and 30 s of silence. |
| `session.py` | Folder layout, `Stream` (stream folder, progress file, run files), run file names, `Timeline` (run files end to end on one audio time axis, gaps, audio time <-> wall time), pid claims. |
| `transcriber.py` | Poll loop over all streams. `StreamState` per stream: progress, `Agreement`, timeline. Each loop gives one pass to each stream that is due. Each pass: span from the end of the last closed line, `read_16k`, backend, `vad.speech_end`, `Agreement.step` decides which words to write and where lines close, append to progress. Then `Layout` puts the lines of all streams on one time axis: rewrite `<name>.txt`, print lines that closed, show the open line and the tail as the draft line. |
| `vad.py` | Silero VAD (bundled with faster-whisper): where the speech in a span ends, with 0.5 s of silence as the end. |
| `loops.py` | Decoder loop checks: `loop_cut` (an n-gram of 1-8 words 3 or more times in a row) and `has_loop` (also zlib compression ratio > 2.4). |
| `console.py` | Terminal output of all live processes: `say` (fixed line), `line` (transcript line) and `Draft` (one line that is replaced; agreed words plain, tail dim). With `LIVE_EVENTS=1`, each output is one JSON line instead (`log`, `line`, `draft`, and from `cli.start` `session` and `state`: recording, stopping, finishing, done, failed). |
| `progress.py` | Append-only JSONL with fsync per write, one file per stream. Truncates a line cut short by a crash. Joins open records into lines. Gives the resume point (end of the last record that is not open) and the prompt. |
| `audio.py` | `read_16k` (span of the timeline as 16 kHz mono, by seek in the run files), `merge` (all run files of one stream into one FLAC in blocks, decode again to check the frame count), `write_aligned` (2 or more streams: each stream and the mix on the reference time axis, in one pass). |
| `finalize.py` | Mark stopped, run the transcriber in this process until done, write the FLAC file(s), clean up. |
| `backends/` | Speech-to-text backends behind one interface. See `backends/ARCHITECTURE.md`. |
| `native/` | macOS system audio helper (Swift, Core Audio process tap). See `native/ARCHITECTURE.md`. |

### Streams (`Layout`, `audio.write_aligned`)

- A stream is one audio source with its own folder, run files, timeline and progress file. `system` is the audio that the meeting app plays. `mic` (with `--mic` or `--mic-only`) is the microphone. With `--mic-only`, `mic` is the only stream, so it is the reference and lines have no label.
- Each stream is transcribed alone. The commit rules below apply per stream.
- The reference is the first stream in `STREAMS` order (`system`, then `mic`) that has audio. Its timeline is the time axis of `<name>.txt` and of `<name>.flac`.
- A time of another stream goes to wall time (wall time of its run file + offset), then to the reference time at that wall time. A wall time in a gap of the reference goes to the start of its next run file.
- With 2 streams, each line starts with the stream label: `Them:` (system) or `Me:` (mic). Lines are sorted by time on the reference axis. With 1 stream, lines have no label.
- Gap lines: each gap of the reference. A gap of another stream gets its own line, `(Me: recording gap HH:MM:SS)`, unless a reference gap is within 2 s of it (both recorders stopped).
- The draft line shows the held words of the stream whose held words start last, with its label.
- `write_aligned` (finalize, 2 or more streams): the reference run files end to end are the time axis. For each block of the reference at wall time W, `WallReader` reads each other stream at W, at the reference rate and channels, with zeros where it has no audio. Each block goes to the file of its stream, and the sum of the blocks, cut to [-1, 1], goes to `<name>.flac`. Thus all files have the same length, and sample N is the same moment in each. Audio of another stream outside the reference run files is in no file.
- All files are written in one pass, then each is decoded again and checked. Only then are they renamed, `<name>.flac` last.
- Transcripts of `<name>.system.flac` and `<name>.mic.flac` (from `python -m app`) are on the same time axis, so `transcript_merge` can merge them by timestamp (see `transcript_merge/ARCHITECTURE.md`).

### Commit rules (`transcriber.Agreement`, `plan_span`)

- A span starts at the end of the last closed line and is at most 30 s. It ends early at the next recording gap.
- The backend gives words with times. A segment with a loop is cut where the loop repeats (`loops.loop_cut`: the first copy stays). `Agreement` keeps in memory the words after the last closed line: `agreed` (2 passes in a row gave them in the same order, compared by letters and digits only; these are the words of the open line, already written) and `tail` (the rest of the last pass, which can still change).
- A pass gives the agreed words again, because it starts at the last closed line. Words that start before the last agreed word end (- 0.1 s) are removed, then up to 5 words at the start that repeat the end of the agreed words. The words left are compared with the tail. Their common start becomes agreed.
- Newly agreed words are written at once into the open line (progress records with `"open": true`). The line closes after a word that ends with `.` `?` `!` when an agreed word comes after it, or when 1.5 s of audio follow it.
- Silero VAD on the span audio gives where speech ends: the start of 0.5 s or more of silence. If the speech ends before the span end (or the VAD finds no speech), all words are written, tail too, and the line closes.
- Capped span (30 s): all words except the last are written and the line closes.
- No word at all: mark the audio done up to span end - 2 s, with a record that has text `""`.
- Final pass (a stopped session reaches its end, or the span ends at a gap): write all words, close the line, mark done to the span end, and empty `agreed` and `tail`.
- Prompt: the last 200 characters of the text of the lines before the span start, in the same recording run. Lines with a loop (`loops.has_loop`) are left out. A word cut by the 200-character limit is left out.
- The draft line shows the open line (plain) and the tail (dim). A line prints as a fixed line when it closes.
- A pass runs only when the timeline grew 1 s or more since the last pass, or the backlog is more than 30 s, or the session is stopped. The folder is polled each 0.2 s.
- Progress records: `{start, end, text}`; `"open": true` means the line goes on in the next record. A record with text `""` closes an open line and marks audio done. On restart, the open records give the agreed words again (times spread evenly over each record).

### Time

- Transcript times are audio time: the sum of run file lengths. A gap between recorder runs closes up on this axis, and shows as a `(recording gap HH:MM:SS)` line at that point.
- Wall time of a run file = arrival time of its first block - the block length. A gap is where the next run file starts later than the end of the one before.
- Loopback and tap only: `RunWriter.anchor` is the wall time of frame 0, set again from each block (arrival - frames written). A block that starts 0.2 s or more after the end of the run gets zeros before it. With no block for 0.5 s, the main loop writes zeros up to now - 0.5 s. Before the first block, the anchor is the stream start.

## Decisions

### macOS capture: Core Audio process tap
- BlackHole needs a driver install, a restart and a Multi-Output Device made by hand, and macOS volume keys do not work on a Multi-Output Device. A process tap (macOS 14.4+) records the mix of all apps with no driver and no change to the sound output; the user grants "System Audio Recording" once.
- The tap is in a small Swift helper, not in Python. Its IO block runs on a dispatch queue that Core Audio calls at audio rate; Python callbacks there (pyobjc) would hold the GIL against the recorder thread. The helper writes to a pipe, so the recorder reads it like any other source and the run file rules do not change.
- The helper writes a JSON header line (rate, channels), then raw float32 frames, and stops when its stdin closes: the same stop signal as the recorder itself.
- The recorder fills silence with zeros as for loopback (see Time), because a tap can deliver no buffers while nothing plays, and the transcript times must follow the wall clock.
- A denied permission gives silence, not an error, from the Core Audio calls. The helper checks the permission first (`TCCAccessPreflight`, loaded at run time from the private TCC framework) and exits with a message. `--status` and `--request` let the desktop app show and ask for the permission.
- In a test (macOS 27, speech played with `afplay` during a 30 s `start --tap` session with `parakeet-mlx`), the transcript was word for word equal to the script, lines printed 2-5 s after speech.
- The first run of the helper, before the permission prompt was answered, wrote the header and no audio, and hung in `AudioDeviceDestroyIOProcID` at the stop. After the permission was granted it stopped cleanly.

### Control by another program: --stdin-stop and JSON events
- The desktop app runs `live start` as a child. Ctrl+C cannot be sent to a child without a console on Windows, so `--stdin-stop` makes the end of stdin act as Ctrl+C. It is also how `live start` stops its own children.
- `LIVE_EVENTS=1` goes to the recorders and the transcriber through the environment, so all processes write JSON lines to the one pipe. The window gets lines, the draft and the state with no parsing of terminal text. Without the variable, the output does not change.

### Windows capture: WASAPI loopback
- Loopback records what a playback device plays, with no driver and no change to the sound output, and the user still hears the audio. VB-Cable (a virtual device, like BlackHole) also works with `--device "CABLE Output"` and no code change; it is the choice when the user must not hear the audio.
- sounddevice (PortAudio) does not give WASAPI loopback devices. pyaudiowpatch is a PyAudio fork that shows each playback device as a `[Loopback]` input device. It is only installed on Windows.
- WASAPI sends no packets to a loopback client while nothing plays. Without filling, the silence would close up and transcript times would not match the wall clock. The fill is in the writer and not in the transcriber, so the run file stays the full recording and the merged `.flac` has the same length as the session.
- The fill uses thresholds (0.2 s gap, 0.5 s idle) so late callbacks during play add no zeros. The anchor is set again from each block, so the drift between the device clock and the system clock does not add up.

### Microphone: a second stream, transcribed alone
- An OS mix (Aggregate Device on macOS, VoiceMeeter on Windows) needs no code, but gives no label per speaker, and speech of 2 people at the same time gets to Whisper as one signal.
- A mix of the 2 streams before Whisper has the same problems, and also needs the 2 device clocks aligned to the frame.
- Separate transcription gives the label `Me` / `Them` from the source, and Whisper hears one speaker at a time. Lines must only be in the correct order, so alignment by wall time is sufficient. The drift between 2 device clocks is about 50 ppm (about 0.2 s per hour).
- One recorder process per stream: if one recorder process exits, the other recorders go on.
- One transcriber process for all streams: one model in memory, and one writer of `<name>.txt`. Two processes would load the model two times and both would write the same `.txt`. A silent mic costs little: the VAD finds no speech and Whisper does not decode.
- No echo removal. With speakers, the mic also records the others, so their lines show two times. `start --mic` shows a warning to use headphones. For the speakers case, `--mic-only` records the mic as the only stream: the mic hears all people, and each line shows one time.
- Mute in the meeting app does not mute the OS microphone, so the mic stream records speech during mute. The warning says this.
- `<name>.flac` is the mix, so `python -m app` on it gives the full meeting. Each stream is also kept alone (`<name>.<stream>.flac`), because a mix cannot be split again.
- The stream files are on the reference time axis, not each on its own. Each stream on its own axis starts at its own recorder start and closes up its own gaps. Then the transcripts of the 2 files cannot be lined up after finalize, because the wall times are only in the run file names, and finalize removes the run files. On one axis, the transcripts merge by timestamp. FLAC stores the zeros where a stream has no audio in almost no space.
- One pass for all files: each block of each run file is read and resampled one time.
- Alignment is by the wall time of each run file start plus the offset in it. The drift between 2 device clocks (about 0.2 s per hour) is not corrected. This is sufficient to put lines in order and to listen, but not for sample-exact work over long sessions.
- `<name>.flac` is written last, so if it exists, the merge is done and checked.
- In a test with the same BlackHole audio on the 2 streams, the mix put the mic copy 0.1 ms from the system copy (the 2 run files started 6 ms apart). With the stream files on one axis: the 3 files had the same frame count, the mic file was 0.17 ms from the system file, and `python -m app` on each gave the same timestamp for each line.

### Disk between recorder and transcriber
- Audio goes to disk before transcription, so a transcriber crash, a slow model or a backlog never loses audio. A crash of the recorder loses only the blocks in its queue that are not yet in the run file (≤ 0.2 s). Checked end to end with `kill -9` on the recorder.
- Native rate and channels (BlackHole: 48 kHz stereo). Resampling to 16 kHz mono happens only in memory for the backend (`soxr`).
- Consecutive run files with the same rate are resampled together in `read_16k`, so there is no filter edge at the join.

### Two processes, linked only by the folder
- A crash in Whisper does not stop the recording. The parent restarts the transcriber (see below); it resumes from the progress file.

### Restart of a child (`cli.Child`)
- Recorders and the transcriber use the same class and the same rules. Only the measure of work is different: a recorder makes run files (count of run files of its stream); the transcriber appends progress records (bytes after the header line, all progress files).
- During the recording, any exit of a child is a failure. A recorder exits normally only when its stdin closes. The transcriber exits with 0 only after `stopped` and all audio done, which comes after the recording. Thus no exit code is checked.
- A child that exits is started again after 2 s. A new recorder run makes a new run file, so the time between the 2 runs is a recording gap, the same as for `start --session`. A new transcriber resumes from the progress files.
- No restart if the child did no work since its first start (no device with that name, a second recorder on the session, a model that does not load). The same error comes at each try, so it stops at once.
- The header line is not work. The transcriber writes it when it makes a progress file, before the model loads (at the first pass). With the full file size, a bad `--model` was restarted 3 times.
- After 3 restarts in a row with no work, the child stays stopped. A restart that does work resets the count. A total limit (the transcriber had 3 per session before) is wrong for a recorder: audio that is not recorded is lost, and a 3 h call with a crash each hour would stop on the 4th crash. The live transcript has the same problem in long calls. For the transcriber, `finalize` can still do the audio later.
- The restart is for a failure of the process (crash, kill), not for a failure of the input device. A new process sees the device list again, but the restart does not try to detect or follow a device change.
- In a test on macOS (a temporary aggregate device as the recorder device, then destroyed during the recording), the recorder did not exit: CoreAudio sent the stream audio from another input, with no error and no status flag. Thus a disconnected device does not cause a restart on macOS.
- The 2 s wait is in the poll loop of the parent, not a sleep, so the parent still watches the other children. Ctrl+C during the wait cancels a recorder restart. A transcriber restart that waits starts at once, because the transcriber must still do the audio that is left.
- Ctrl+C does not change: it goes only to the parent (the children are in their own process group). The parent closes the stdin of the process of each child that runs now, the restarted one too.
- Verified end to end with `start --device BlackHole` and speech: `kill -9` of the recorder (restart, `(recording gap 00:00:02)`, finalize merged 2 run files); `kill -9` of the transcriber (restart, transcript complete); Ctrl+C in the 2 s wait of each; bad `--device` and bad `--model` (no restart).

### Raw PCM run files, no FLAC snippets
- The first version wrote 5 s FLAC snippets. The transcriber saw audio only after a snippet closed, so each line waited up to 5 s more.
- Raw PCM has no header, so the transcriber can read it while the recorder appends to it. The recorder flushes each block to the OS, and the reader counts only complete frames (file size // frame size). Thus a read gets audio up to about 0.2 s old.
- A read is 1 seek and 1 read of the span (at most 30 s), so its cost does not grow with the file.
- An intermediate version kept FLAC snippets and added a `.pcm` for the open snippet. The change from `.pcm` to FLAC needed: "prefer the FLAC if both exist", "only the next sequence", a read that falls back to the FLAC, a retry when Windows refuses to remove an open file, and a seal step for a `.pcm` left by a crash. One run file per recorder run needs none of these: the recorder never removes or renames a file while it runs.
- A pipe from recorder to transcriber was rejected. Audio in the pipe is lost when the transcriber restarts; the join of disk and pipe needs frame numbers; a full pipe (64 KB on macOS, about 0.2 s at 48 kHz stereo) blocks the recorder and loses audio. The run file has none of these, and the folder stays the only link.
- Float32 as from the audio callback, so the recorder does no conversion per block. The rate and channels are in the name, because the file has no header.
- Cost: disk during the recording is about 1.4 GB per hour at 48 kHz stereo (FLAC 24-bit: about 0.45 GB per hour). Raw PCM has no checksum; the merge still decodes the result again and checks the frame count.
- One file per run, not one per fixed length: a new file is needed only where the wall time can jump (a new run). No file size limit applies on APFS or NTFS.
- End-to-end test (2 min 36 s of speech played into BlackHole, `base` model), delay from the end of a spoken line to its print: 5 s FLAC snippets: median 6.3 s, p90 22.8 s, max 37.0 s. Raw PCM: median 3.6 s, p90 7.2 s, max 12.3 s. Most of the delay that was left came from the segment commit rule. See "Commit rules" below for the word rule that replaced it.

### Polling, not push
- The transcriber must scan the folder at start and after a crash anyway. A notification would be a second way to find the same files, and it is lost when the transcriber is down. Polling every 0.2 s adds at most 0.2 s; one Whisper pass takes about 0.5 s.
- A live pass waits for 1 s of new audio. Each pass transcribes again all audio after the last commit, so a pass for each poll uses CPU for little new text.

### No state file
- The resume point of a stream is the last `end` in its `transcript.<stream>.partial.jsonl`. The run file and offset for it come from the run file sizes.
- Silence is written as records with text `""`. Without them, the resume point would stay at the last speech and each pass would grow over all the silence after it.

### Commit rules
- The words at the end of a span can be cut at the span end, so they wait for more audio. A word is written only when 2 passes agree on it, or when the VAD finds the end of speech.
- The first rule committed by segment: all segments but the last. Whisper decides where a segment ends, and it often kept 2 or more sentences, 10 s or longer, as one segment. That segment was always the last one, so it waited. In the end-to-end test, a line showed only 14 s after speech started.
- Agreement by word (the "LocalAgreement-2" method from the `whisper_streaming` project) writes a word soon after it is spoken, independent of where Whisper ends segments.
- End-to-end test (73 s of real speech played into BlackHole, `base` model), delay from the last word of a line to its print, 2 runs each: segment rule median 2.8 s, p90 4.1-6.2 s, max 10-11 s; word agreement (lines at agreed sentence ends) median 2.4-3.2 s, p90 3.0-4.9 s, max 4.0-5.5 s. Segment lines were cut mid-sentence. The word rule includes the token cap in `backends/` (see there).
- The span still starts at the last closed line, not at the last agreed word. Thus Whisper hears full sentences, and a restart resumes from the progress file. The open line is in the progress file, so a restart gets its words back for the overlap check.
- "No speech" for the silence record is decided only from no words at all. A span with words that are not written must not move forward: that skipped a full sentence in an end-to-end test.
- A span never crosses a recording gap. Across a gap, Whisper joined the last words of one run to the first words of the next.
- The prompt uses only lines from the same recording run. With the text from before a gap as prompt, Whisper repeated the prompt instead of the new speech.

#### Open line, VAD pause, loop cut, 200-character prompt (2026-11-20)
- Benchmark: `tests/bench_live.py` on the 2x speed fixture, `base`, real-time feed. Write delay = word in the transcript - word audio on disk.

  | Version | WER | Write delay median / p90 / max | Line delay median | End lag |
  |---|---|---|---|---|
  | before (temperature 0, line written at agreed sentence end, 8 s clause rule, pause = last word end 1.5 s before span end, 5-line prompt) | 0.377 | 3.9 / 5.8 / 9.5 s | 4.0 s | 0.5 s |
  | after (2 runs) | 0.151-0.162 | 2.9-3.5 / 5.2-5.8 / 6.8-10.5 s | 3.9-4.9 s | 0.2-5.8 s |

- Most of the WER before was one loop: "I mean, I mean, …" over 30 s at the end, written as lines. The loop cut, the temperature fallback and the prompt filter stop it.
- Agreed words are written at once into an open line, so the transcript and the draft get a word one pass after it agrees, not after its sentence ends. The 8 s clause rule is gone: it only existed to bound the wait for a sentence end.
- A sentence end at the end of the agreed words does not close the line at once. Whisper often writes a period at the cut end of a span ("who designed C." before "designed it at a time when you"). When the line closed there, the next span started mid-word and Whisper dropped the rest of the sentence ("designed it at a time when you" was lost).
- It closes after 1.5 s of audio without an agreed next word. Whisper often stops its output at a period and leaves the rest of the span out; passes then change between the short and the full text, and no next word agrees. Without this limit the open line waited up to 25 s (write delay median 7.8 s, p90 20 s).
- The pause comes from Silero VAD, not from the last word end. Word end times from Whisper are not exact, and a word cut at the span end can still end before it. The VAD runs on the span audio again (10 ms for 5 s, 60 ms for 30 s) because faster-whisper does not return its own VAD chunks, and its options (2 s minimum silence, 0.4 s pad) are for cutting audio, not for a pause. The pause is 0.5 s; at 2x speed the fixture rarely has one, so most lines close by sentence end.
- The VAD speech end is compared with the span length in samples. The span end in seconds and the resampled sample count differ by a rounding error; a check against the span end in seconds took every pass as a pause and wrote unagreed tails (WER 0.40).
- A loop is an n-gram (1-8 words) 3 or more times in a row. 2 times is normal speech ("and and", "I mean, I mean"). The first copy stays, because it is often real speech that Whisper then repeats.
- The prompt is the last 200 characters (as in `whisper_streaming`), not a fixed number of lines: 5 long lines filled the prompt with old text, 5 short lines gave almost none. A line with a loop in the prompt makes Whisper continue the loop.

### Output
- `<name>.txt` uses `app.stages.common.write.write_full`, so the format is the same as `python -m app` (header `<name>-live`, `[HH:MM:SS] text`). It is rewritten in full after each commit (atomic `.tmp` + rename), so it is always complete.
- The live transcript is a draft. A better transcript comes from `python -m app` on the merged `.flac`, so there is no second pass here.

### Draft line
- A live caption must show text while the speaker talks. The commit rule holds words until they are agreed and a line is complete, so without a draft, text showed only when a line was written.
- Commit of all words on each pass was rejected. A pass covers about 1 s of new audio and often ends mid-word. The saved lines would get broken or doubled words. The draft gives the same delay on the screen and keeps the commit rule.
- Agreed words are plain and the tail is dim, so the reader sees which words can still change.
- The draft is only on the screen. `<name>.txt` and the progress file get only committed lines.
- All live processes write to one terminal. Each fixed line starts with carriage return + erase line, so a log line from the recorder or the parent replaces the draft and does not join it. The transcriber shows the draft again at its next pass.
- The draft is cut to the terminal width, and keeps the end of the text. A draft that wraps cannot be erased with one erase line.
- When stdout is not a terminal (a pipe or a file), no draft is written. Thus logs keep only fixed lines.

### Finalize
- The merge writes `<name>.flac.tmp` (FLAC 24-bit), decodes it again and compares the frame count, then renames it. Run files are removed only after that. If the check fails, all work files stay.
- The merge reads and resamples in blocks of 1 Mi frames (`soxr.ResampleStream` when a run has another rate), so memory does not grow with the length. A 3 h run file is about 4 GB.
- If `<name>.flac` exists, it was checked before, so `finalize` only cleans up. It does not transcribe again: after a crash during clean-up, the progress file may already be gone.
- A finalized session cannot be continued with `--session`.

### Reuse of app
- From `app`: `models` (`Line`, `timestamp`, `clean`, `safe_name`, `TARGET_LANGUAGE`), `stages.common.transcribe` (`load_model`, `complete_length`, `is_segment`) and `stages.common.write.write_full`. `app.stages.common.transcribe.transcribe` itself is not used: it takes one complete file, and here the audio grows during the run.
