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
- `test_live.py` tests `live/` with a `FakeBackend` and FLAC snippets written by `write_snippet`. The sample value of each snippet is (timeline time / 1000), so the fake backend can read where its audio comes from and name its segments `at <time>`.

| Test group | Path tested |
|---|---|
| snippet writer | fixed-length snippets and a short last one; a second run continues the sequence and makes a gap; `.tmp` files ignored |
| live `.pcm` file | writer keeps the open snippet in a `.pcm` with exact frames and removes it after the FLAC; a block split across snippets; `.pcm` used only as the next sequence, FLAC preferred; only complete frames counted; `read_16k` from `.pcm` and from the FLAC after it is sealed; `seal_live` saves a left `.pcm` and removes one whose FLAC exists |
| commit rules (`choose`) | last segment left open; committed when silence follows; final pass; no segment advances with a 2 s tail; one open segment does not advance; capped single segment; empty text |
| transcriber loop (`run`) | stopped session done to the end; live pass then continue; `.pcm` audio transcribed; no new pass before 1 s of new audio; resume after a cut-short progress line; pass stops at a gap; prompt not used across a gap; gap line in `.txt` |
| audio and finalize | exact merge; other rate and channels; `read_16k` across snippets; finalize writes `.txt` and `.flac` and removes work files; finalize saves a left `.pcm` before the merge; failed merge check keeps everything; finalize after merge only cleans up |

- `test_temp_files.py` tests `temp_files.work_files` and `temp_files.name_from_log` on files in `tmp_path`. These find the work files that the web Delete button removes.

## Decisions

- Tests go through `pipeline.run`, not `write_full` alone. Thus they cover the same write and cleanup steps as a real run.
- The fake source has no network and no Whisper, so the tests run in under 1 s and give the same result offline.
- `test_live.py` does not open an audio device or load Whisper. The recorder stream and the faster-whisper backend are checked end to end by hand: play speech into BlackHole 2ch and run `python -m live start`.
