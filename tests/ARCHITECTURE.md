# tests/

## How it works

- Run with `python -m pytest tests`. pytest is in `requirements-dev.txt`, not `requirements.txt`.
- `test_output_files.py` registers a `FakeSource` as the only source (`monkeypatch` on `pipeline.SOURCES`), then calls `pipeline.run`. Each test uses its own `tmp_path` as `--outdir`.

| Test group | Path tested |
|---|---|
| header line and first write | `<name>.txt` with line 1 `<name>-<id>` |
| rerun of the same input | same file replaced, no new file |
| conflict | other input, same name: `<name>-<id>.txt`; existing file not changed; rerun of either input replaces only its own file; 3 inputs give 3 files; existing file with no header, empty, or a longer header counts as a conflict; unsafe id characters removed from the file name but kept in the header |
| temp cleanup | progress file and downloaded audio removed; `temp/` removed only when empty |
| `output_name` | missing date, author, title; unsafe characters |

- `test_podcast_feed_search.py` replaces `podcast_feed._apple_search` with fixed results and tests the feed order of `search_feed_urls`: episode-title match first, then show-name match, no duplicates, at most `MAX_FEEDS`. It also tests that a failed request gives no results.
- `test_live.py` tests `live/` with a `FakeBackend` and raw `.pcm` run files. An autouse fixture replaces `vad.speech_end` with `fake_speech_end`, which follows the speech times of `FakeBackend` (speech ends where 0.5 s or more of audio follow the last word). The sample value is (timeline time / 1000), so the fake backend can read where its audio comes from. Each 2 s it gives a segment with the words `at` and `<time>.`. `GrowingBackend` also adds audio after each call, so 2 live passes can run in one test.

| Test group | Path tested |
|---|---|
| run writer | blocks appended to one run file, readable while open; a second run continues the sequence and makes a gap; other files ignored; only complete frames counted; empty run file ignored; growing file extends the timeline |
| commit rules (`Agreement`) | first pass writes nothing; agreed words written at once into an open line; line closes at a sentence end with an agreed word after it, or 1.5 s of audio after it; agreement by letters and digits; stops at the first changed word; repeated agreed words dropped; VAD speech end writes all and closes the line (also with no new word); last word end alone is not a pause; final writes all and resets; capped keeps the last word; silence advances with a 2 s tail; empty word text left out; segment cut at a loop; open records give the agreed words on resume |
| loops (`loops`) | loop cut keeps the first copy; 2 copies are not a loop; loop by n-gram or compression ratio |
| progress lines and prompt | open records join the next record; a `""` record closes an open line; `end` ignores open records; prompt is the last 200 characters of lines before the span, lines with a loop left out |
| terminal output (`console`) | `fit` keeps the end of a long draft; agreed words plain and tail dim, also when cut; on a terminal the draft shows, then a fixed line replaces it; no draft when stdout is not a terminal |
| transcriber loop (`run`) | stopped session done to the end; 2 live passes write agreed sentences, then continue; no new pass before 1 s of new audio; resume after a cut-short progress line; pass stops at a gap; prompt not used across a gap; gap line in `.txt` |
| audio and finalize | exact merge; other rate and channels; merge in blocks with resampling equals one-piece resampling; `read_16k` across run files; finalize writes `.txt` and `.flac` and removes work files; failed merge check keeps everything; finalize after merge only cleans up |
| two streams (system + mic) | wall time to timeline time and back, also in a gap and after the end; lines of both streams labelled and in wall time order; 1 stream has no labels; a mic-only gap gets its own line, a gap of both streams only one; the draft shows the latest held words with the label; `write_aligned` gives each stream and the mix the same length on the reference axis, puts the mic at its wall time, converts rate and channels, follows gaps, cuts the mix to full scale, and renames no file if one check fails; finalize writes each stream and the mix on one axis; finalize waits for the mic recorder; the mic writer writes to `mic/`; `start` gives the recorder arguments per stream |
| restart of a child (`cli.Child`) | restart after the delay; any exit code restarts; no restart if the first start did no work; no more restarts after 3 in a row with no work; a restart with work resets the count; stop closes stdin and cancels a planned restart; a planned restart can start at once; transcriber work is progress bytes after the header line |

- `test_faster_whisper.py` runs the real `FasterWhisper` backend with the `tiny` model on `fixtures/speech_22s.flac` (macOS `say` speech, 16 kHz mono) and a prompt over 223 tokens. It checks the token cap stays inside the 448-token Whisper limit.
- `test_live_bench.py` (marker `bench`, left out by `pytest.ini` unless `-m bench`) runs `bench_live.bench`: the 2x speed fixture `fixtures/20260801-Amir’s Lab-...flac` is appended to a run file in 0.1 s blocks at the pace of the audio, while `transcriber.run` with the real backend (`base`) and the real VAD reads the same session. Checks WER against the fixture `.txt` (lower case, letters and digits, fillers `uh`/`um` left out) ≤ 0.20, write delay median ≤ 4.5 s and p90 ≤ 8 s, end lag ≤ 10 s.
- `test_temp_files.py` tests `temp_files.work_files` and `temp_files.name_from_log` on files in `tmp_path`. These find the work files that the web Delete button removes.
- `test_transcript_merge.py` tests `transcript_merge` on `.txt` files in `tmp_path`: header and blank lines skipped, a file with no header, a line with no timestamp joined to the line before, text before the first timestamp is an error, sort by time with labels, same-second order, default name, `LABEL=PATH` parsing, the CLI output file and folder, no replacement of a transcript with another header, missing file.

- `conftest.py` sets `QT_QPA_PLATFORM=offscreen`, so Qt tests open no window.
- `test_parakeet.py`: token to word joining, sentences, model folder check, the live backend and the file windows with a `FakeParakeet`. The `model` marker test needs `PARAKEET_MODEL_DIR`.
- `test_live_events.py`: JSON events of `console`, `start --stdin-stop` with fake children (states recording, stopping, failed with no audio), the tap stream with a fake helper script (header, frames cut across reads, helper error).
- `test_desktop_entry.py`: `-m module` and `-u` in the entry point, exit code kept.
- `test_desktop_models.py`: download with progress, cancel and resume (Range), dropped connection, HTTP error, wrong size, delete, catalog per platform. A local HTTP server stands in for Hugging Face.
- `test_desktop_core.py`: settings file, child command lines per source and job, child environment, history scan and search, `process.Child` (line split, stdin close, kill of grandchildren, exit code).
- `test_desktop_jobs.py` and `test_desktop_ui.py` (pytest-qt): job order and status, live events to signals; main window list, search, open, drop, job cards, record button with a fake `live start` script, onboarding Continue.

## Decisions

- Desktop tests use small Python scripts as child processes, not the real CLIs: the window code is tested without models or audio devices. The real chain (window, packaged worker, tap, model) is checked by hand and by `packaging/build.py --smoke`.

- The live benchmark is the default check for live accuracy and speed. The fixture is real speech at 2x speed: fast speech, short pauses, and an outro after the speech, so loops, cut words and missed pauses show up in 100 s of audio. The reference is the YouTube caption text, so a WER of 0 is not possible; offline `base` on the whole file (one call, default options) gives 0.12.
- The benchmark feeds the audio at real pace, because the delay depends on how much audio arrives during each pass. It runs the transcriber in the same process (not `python -m live start` with an audio device), so it needs no BlackHole and gives the same input each run. Results still change from run to run by about ±1.5 points WER and ±0.5 s median delay (CPU load and pass timing change which spans are cut where).
- `bench` is not in the default run: it takes about 2 min and loads the model.

- Tests go through `pipeline.run`, not `write_full` alone. Thus they cover the same write and cleanup steps as a real run.
- The fake source has no network and no Whisper, so the tests run in under 1 s and give the same result offline.
- `test_live.py` does not open an audio device or load Whisper. `test_faster_whisper.py` loads the `tiny` model (download on first run) because the 448-token limit is in faster-whisper and a fake backend cannot show it. The WASAPI loopback stream uses a fake `pyaudiowpatch` module. The recorder stream and the faster-whisper backend are checked end to end by hand: play speech into BlackHole 2ch and run `python -m live start` (macOS; with `--mic BlackHole` the 2 streams get the same audio, so their lines and the mix can be compared), or play speech and run `python -m live start --loopback` (Windows).
