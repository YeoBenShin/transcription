# stages/

## How it works

| Stage | Input | Output |
|---|---|---|
| `captions.fetch_captions` | YouTube video id | `List[Line]`, or `None` if there are no usable captions |
| `download.download_audio` | any URL that yt-dlp supports, target folder | path of the audio file |
| `transcribe.transcribe` | audio/video file path, model size | `List[Line]` |
| `chunk.chunk` | lines, window length in seconds | `List[List[Line]]` |
| `write.write_chunks` | chunks, source_id, window length, outdir | written file paths |

Stages do not import from `sources/` or `pipeline.py`.

## Decisions

### captions
- Captions are tried before Whisper because they are free and fast. Their quality can be lower than Whisper `large` (auto-generated captions make errors with domain terms).
- If there is no English track, the stage uses YouTube's translation of the first translatable track.

### download
- yt-dlp saves the best audio stream with no re-encoding. Thus ffmpeg is not necessary for this step. faster-whisper decodes the file through PyAV.
- It takes a URL, not a YouTube id, so that podcast and other yt-dlp sources can use it later.

### transcribe
- faster-whisper on CPU with int8: works on any machine with no GPU setup.
- `vad_filter=True` skips silence and music. This decreases hallucinated text in long silent parts.
- Language is fixed to English (`models.TARGET_LANGUAGE`).
- `faster_whisper` and `yt_dlp` are imported inside the functions. Thus the caption-only path does not load them.

### chunk / write
- Chunks are fixed time windows, aligned to multiples of the window length. Empty windows are dropped, but chunk file names keep the real window times.
- `write_chunks` deletes old `chunk_*.txt` files in the target folder first. This prevents files from an earlier run with a different `--chunk-minutes` from remaining.
