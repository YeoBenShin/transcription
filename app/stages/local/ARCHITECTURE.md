# stages/local/

## How it works

Stages that only the local file source calls. `sources/local_file.py` calls `file_info`, then `common.transcribe`.

| Stage | Input | Output |
|---|---|---|
| `file_info.fetch_file_info` | file path | `FileInfo` (date, author or `None`) |

## Decisions

### file_info
- Reads container tags with PyAV, which faster-whisper already installs. No new dependency and no `ffprobe` binary.
- Date: the first `creation_time` or `date` tag that starts with `YYYY-MM-DD`. A recorder or phone writes the recording time there, and it does not change when the file is copied. If there is no such tag: the file creation date (`st_birthtime`, macOS and Windows), else the modification date. A tag with only a year is ignored, because it does not give a full date.
- Author: the first non-empty `artist`, `album_artist`, `author` or `composer` tag. Most recordings have none, so the name then has `Unknown`.
- A file that PyAV cannot open gives no tags, not an error. The transcribe stage then reports the real decode error.
