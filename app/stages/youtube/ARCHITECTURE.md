# stages/youtube/

## How it works

Stages that only the YouTube source calls. `sources/youtube.py` calls `video_title`, then `captions`; if there are no captions it calls `download` and then `common.transcribe`.

| Stage | Input | Output |
|---|---|---|
| `video_title.fetch_video_title` | YouTube video id | title, or `None` on failure |
| `captions.fetch_captions` | YouTube video id | `List[Line]`, or `None` if there are no usable captions |
| `download.download_audio` | any URL that yt-dlp supports, target folder, file stem | path of the audio file |

## Decisions

### video_title
- Uses the YouTube oEmbed endpoint. It needs no key and is one small request. yt-dlp metadata would also give the title, but the caption path does not otherwise load yt-dlp.
- A failure returns `None` and does not stop the run. The output folder then uses the video id.

### captions
- Captions are tried before Whisper because they are free and fast. Their quality can be lower than Whisper `large` (auto-generated captions make errors with domain terms).
- If there is no English track, the stage uses YouTube's translation of the first translatable track.

### download
- yt-dlp saves the best audio stream with no re-encoding. Thus ffmpeg is not necessary for this step. faster-whisper decodes the file through PyAV.
- It takes a URL, not a YouTube id, so that podcast and other yt-dlp sources can use it later.
- `yt_dlp` is imported inside `download_audio`. Thus the caption path does not load it.
