# stages/

## How it works

| Stage | Input | Output |
|---|---|---|
| `captions.fetch_captions` | YouTube video id | `List[Line]`, or `None` if there are no usable captions |
| `download.download_audio` | any URL that yt-dlp supports, target folder | path of the audio file |
| `spotify_episode.fetch_spotify_episode` | Spotify episode id | `SpotifyEpisode` (title, show, release time, duration) |
| `podcast_feed.find_feed_episode` | show name, episode title, release time | `FeedEpisode` (feed URL, enclosure URL, transcript links), or `None` |
| `feed_transcript.pick_transcript` | transcript links | best timestamped link, or `None` |
| `feed_transcript.fetch_feed_transcript` | transcript link | `List[Line]`, or `None` |
| `http.download_file` | direct file URL, target folder, file stem | path of the downloaded file |
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

### spotify_episode
- Reads the JSON in the `__NEXT_DATA__` script tag of `open.spotify.com/embed/episode/<id>`. This page needs no login and has the title, show name and release time. The Spotify Web API needs app credentials, and the oEmbed endpoint has no show name.
- Sends a browser User-Agent. The embed page is made for browsers.

### podcast_feed
- Finds feeds with the Apple Podcasts search API (`itunes.apple.com/search`). It needs no key. Podcast Index also has a search API, but it needs a key and secret.
- One show name can give many search results (copies, summary shows). Results with the same normalized name are tried first, and a feed is used only if it contains the episode. At most `MAX_FEEDS` feeds are fetched.
- Episode match: exact normalized title first (case, punctuation and HTML entities ignored, because feeds encode `&` as `&#038;` and Spotify does not). If no exact match: the most similar title among items published within 2 days of the Spotify release time.
- Feeds are parsed with `defusedxml`, because the XML comes from third parties.

### feed_transcript
- Reads the Podcasting 2.0 `<podcast:transcript>` tag. Preference: JSON, then WebVTT, then SRT. HTML and plain-text transcripts have no timestamps, so chunking cannot use them; they are ignored and Whisper runs.
- JSON transcripts are often one segment per word. Words are grouped into lines at a speaker change, a pause over 2 s, a sentence end after 5 s, or 30 s. Each line gets a `Speaker: ` prefix when the JSON names the speaker, so a chunk read alone still shows who talks.
- SRT/VTT files are decoded as UTF-8. HTTP servers often send them without a charset, and `requests` then assumes Latin-1.

### http
- `download_file` streams the enclosure URL with `requests`. yt-dlp is not necessary for a direct mp3 link. Enclosure URLs often go through tracking redirects; the file extension comes from the final URL.

### transcribe
- faster-whisper on CPU with int8: works on any machine with no GPU setup.
- `vad_filter=True` skips silence and music. This decreases hallucinated text in long silent parts.
- `load_model` caches the model (`lru_cache`). A batch run loads the model once, not once per file.
- Language is fixed to English (`models.TARGET_LANGUAGE`).
- `faster_whisper` and `yt_dlp` are imported inside the functions. Thus the caption-only path does not load them.

### chunk / write
- Chunks are fixed time windows, aligned to multiples of the window length. Empty windows are dropped, but chunk file names keep the real window times.
- `write_chunks` deletes old `chunk_*.txt` files in the target folder first. This prevents files from an earlier run with a different `--chunk-minutes` from remaining.
