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
- `test_temp_files.py` tests `temp_files.work_files` and `temp_files.name_from_log` on files in `tmp_path`. These find the work files that the web Delete button removes.

## Decisions

- Tests go through `pipeline.run`, not `write_full` alone. Thus they cover the same write and cleanup steps as a real run.
- The fake source has no network and no Whisper, so the tests run in under 1 s and give the same result offline.
