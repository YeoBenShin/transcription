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
- `test_live.py` tests `live/` with a `FakeBackend` and raw `.pcm` run files. The sample value is (timeline time / 1000), so the fake backend can read where its audio comes from. Each 2 s it gives a segment with the words `at` and `<time>.`. `GrowingBackend` also adds audio after each call, so 2 live passes can run in one test.

| Test group | Path tested |
|---|---|
| run writer | blocks appended to one run file, readable while open; a second run continues the sequence and makes a gap; other files ignored; only complete frames counted; empty run file ignored; growing file extends the timeline |
| commit rules (`Agreement`) | first pass writes nothing; agreed words up to a sentence end written; agreement by letters and digits; stops at the first changed word; repeated agreed words dropped; pause writes all as sentence lines; 8 s limit breaks at a clause mark, or writes all; final writes all and resets; capped keeps the last word; silence advances with a 2 s tail; empty word text left out |
| terminal output (`console`) | `fit` keeps the end of a long draft; agreed words plain and tail dim, also when cut; on a terminal the draft shows, then a fixed line replaces it; no draft when stdout is not a terminal |
| transcriber loop (`run`) | stopped session done to the end; 2 live passes write agreed sentences, then continue; no new pass before 1 s of new audio; resume after a cut-short progress line; pass stops at a gap; prompt not used across a gap; gap line in `.txt` |
| audio and finalize | exact merge; other rate and channels; merge in blocks with resampling equals one-piece resampling; `read_16k` across run files; finalize writes `.txt` and `.flac` and removes work files; failed merge check keeps everything; finalize after merge only cleans up |

- `test_temp_files.py` tests `temp_files.work_files` and `temp_files.name_from_log` on files in `tmp_path`. These find the work files that the web Delete button removes.

## Decisions

- Tests go through `pipeline.run`, not `write_full` alone. Thus they cover the same write and cleanup steps as a real run.
- The fake source has no network and no Whisper, so the tests run in under 1 s and give the same result offline.
- `test_live.py` does not open an audio device or load Whisper. The recorder stream and the faster-whisper backend are checked end to end by hand: play speech into BlackHole 2ch and run `python -m live start`.
