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

## Decisions

- Tests go through `pipeline.run`, not `write_full` alone. Thus they cover the same write and cleanup steps as a real run.
- The fake source has no network and no Whisper, so the tests run in under 1 s and give the same result offline.
