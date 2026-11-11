# stages/youtube/

## How it works

Stages that only the YouTube source calls. `sources/youtube.py` calls `video_info`, then `captions`; if there are no captions it calls `download` and then `common.transcribe`.

| Stage | Input | Output |
|---|---|---|
| `video_info.fetch_video_info` | YouTube video id | `VideoInfo` (upload date, channel, title); each field `None` if not known |
| `captions.fetch_captions` | YouTube video id | `List[Line]`, or `None` if there are no usable captions |
| `download.download_audio` | any URL that yt-dlp supports, target folder, file stem | path of the audio file |

## Decisions

### video_info
- Uses yt-dlp metadata (`extract_info(download=False, process=False)`). The output name needs the upload date, and the oEmbed endpoint has only title and channel. `process=False` skips format selection, so it takes about 1 s.
- If yt-dlp fails, it uses the oEmbed endpoint for title and channel, with no date. If both fail, all fields are `None`. The run does not stop; `output_name` fills in the missing parts.

### captions
- Captions are tried before Whisper because they are free and fast. Their quality can be lower than Whisper `large` (auto-generated captions make errors with domain terms).
- If there is no English track, the stage uses YouTube's translation of the first translatable track.

### download
- yt-dlp saves the best audio stream with no re-encoding. Thus ffmpeg is not necessary for this step. faster-whisper decodes the file through PyAV.
- It takes a URL, not a YouTube id, so that podcast and other yt-dlp sources can use it later.
- `%` in the target path is escaped as `%%`. yt-dlp reads `outtmpl` as a template, and titles can contain `%`.
