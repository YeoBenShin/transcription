# transcript_merge/ (package)

## How it works

```
python -m transcript_merge Them=<a>.txt Me=<b>.txt [--name NAME] [--outdir DIR]
   │  (cli.main)
   ├─ parse_source: 'LABEL=PATH' or 'PATH' ─► Source(path, label)
   ├─ merge.read_lines per source: app format ─► Line(start seconds, text)
   ├─ merge.merge: label each line, sort by (time, source order, file order)
   └─ app.stages.common.write.write_full ─► <outdir>/<name>.txt, header '<name>-merged'
```

| File | Role |
|---|---|
| `cli.py` | Arguments, file checks, output name and folder, write. |
| `merge.py` | `read_lines` (parse one transcript), `merge` (all sources in time order, with labels), `default_name` (common start of the input names). |

- Input format is the output of `python -m app` and `python -m live`: an optional header line, a blank line, then `[HH:MM:SS] text` lines. A line with no timestamp continues the line before it.
- The default output name is the common start of the input names, with `.`, `-`, `_` and spaces removed from its end. `X.system.txt` and `X.mic.txt` give `X.txt`. The default folder is the folder of the first input.
- The output goes through `write_full`, so `<name>.txt` that holds another transcript (other header) is not replaced. The merge then goes to `<name>-merged.txt`.

## Decisions

- The inputs must be on one time axis. The package does not shift times. `live` writes `<name>.system.flac` and `<name>.mic.flac` on one axis for this reason (see `live/ARCHITECTURE.md`, "Streams").
- A separate package, not part of `live/` or `app/`: it reads only `.txt` files, and works on any transcripts on one axis, for example `python -m app` output of each stream file with a larger model than the live run.
- Labels come from the command line (`LABEL=`), not from the file names, so the user picks the names (`Me`, `Them`, a person's name).
- Lines with the same second keep the order of the sources on the command line, then the order in the file. The app timestamps have 1 s resolution, so the order inside 1 s is not known.
- The output header is `<name>-merged`. With the header check in `write_full`, a second run of the same merge replaces its own output, and the live transcript `<name>.txt` (header `<name>-live`) is kept.
