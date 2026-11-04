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

#### Model options

To change the backend, change `load_model` and the segment loop in `transcribe.py`. Each option must give a start time (seconds) and text per segment, so that `Line(start, text)` can be made. Model weights download once to `~/.cache/huggingface/hub` (or the path given) and then load from disk.

Summary. "Mac" is Apple Silicon (e.g. M3 Pro). "Windows GPU" is an NVIDIA card with CUDA 12 (e.g. RTX 3060 or better). "Windows CPU" is a Windows machine with no NVIDIA card.

| Option | Runs on | English accuracy | Speed on Mac | Speed on Windows GPU | Speed on Windows CPU | Segment timestamps | Cost | Main limit |
|---|---|---|---|---|---|---|---|---|
| faster-whisper (current) | CPU, CUDA | Whisper level | Medium. No Mac GPU | Very fast | Medium | Yes | Free | CPU only on Mac |
| mlx-whisper | Apple GPU | Whisper level | Fast | Not available | Not available | Yes | Free | Apple Silicon only. No built-in VAD |
| parakeet-mlx (NVIDIA Parakeet TDT) | Apple GPU | Better than Whisper large-v3 | Very fast | Not available | Not available | Yes | Free | Apple Silicon only. v2 is English only |
| NeMo (Parakeet / Canary) | CUDA GPU | Best open models | Slow | Very fast (use WSL2) | Slow | Yes | Free | Large install. Linux first; on Windows use WSL2 |
| pywhispercpp (whisper.cpp) | CPU, Metal, CUDA/Vulkan | Whisper level | Fast | Fast (needs a build with CUDA or Vulkan) | Medium | Yes (10 ms units) | Free | Python binding is less mature |
| openai-whisper | PyTorch CPU/CUDA | Whisper level | Slow | Fast | Very slow | Yes | Free | Reference code. Slowest |
| transformers pipeline | PyTorch CPU/MPS/CUDA | Model dependent | Medium | Fast | Slow | Yes | Free | Large install (torch). Many models |
| WhisperX | CPU, CUDA | Whisper level | Medium | Very fast | Medium | Yes, plus word level | Free | Diarization needs Hugging Face token |
| OpenAI API | Cloud | Whisper level or better | Network bound | Network bound | Network bound | `whisper-1` only | Paid per minute | 25 MB file limit. Audio leaves machine |
| Groq API | Cloud | Whisper level | Very fast | Very fast | Very fast | Yes | Paid, low | File size limit. Audio leaves machine |
| Deepgram / AssemblyAI | Cloud | High. Good diarization | Fast | Fast | Fast | Yes, plus speakers | Paid per minute | Vendor lock-in. Audio leaves machine |

Windows notes:
- On an NVIDIA GPU, faster-whisper is the best local choice on Windows: `device="cuda", compute_type="float16"`. It needs the CUDA 12 cuBLAS and cuDNN 9 DLLs on `PATH` (e.g. `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`, or the CUDA Toolkit install).
- With no NVIDIA GPU (Intel or AMD only), use faster-whisper on CPU with `int8`, or a cloud API. PyTorch options are much slower on CPU.
- Parakeet on Windows: use NeMo inside WSL2 with an NVIDIA GPU. The MLX ports do not run on Windows.

Whisper sizes (faster-whisper, mlx-whisper, openai-whisper): `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`. A `.en` suffix (`small.en`) gives English-only models that are slightly better for English up to `medium`. `large-v3-turbo` is near `large-v3` accuracy at much higher speed; it is the best size for most uses. `distil-large-v3` is a faster English-only alternative.

##### faster-whisper (current)
- `pip install faster-whisper`
- Best for: any machine, no GPU setup. Built-in VAD (`vad_filter=True`).
- Not for: speed on Mac. CTranslate2 has no Metal backend.
```python
from faster_whisper import WhisperModel
model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")  # CUDA: device="cuda", compute_type="float16"
segments, _ = model.transcribe(path, language="en", vad_filter=True)
# segment.start, segment.text
```
Options: `local_files_only=True` (never download), `download_root="/path"` (model folder), `cpu_threads=N`, `beam_size=5`.

##### mlx-whisper
- `pip install mlx-whisper`
- Best for: Whisper on Apple Silicon GPU. Same accuracy as faster-whisper, several times faster on M-series chips.
- Not for: Linux or Windows. No VAD, so long silences can give hallucinated text.
```python
import mlx_whisper
result = mlx_whisper.transcribe(path, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="en")
# result["segments"]: each has "start", "text"
```
No model object to cache; the library caches loaded weights itself.

##### parakeet-mlx (NVIDIA Parakeet TDT on Apple Silicon)
- `pip install parakeet-mlx`
- Best for: the fastest and most accurate English option on a Mac. Parakeet TDT 0.6B is near the top of the Hugging Face Open ASR leaderboard and is much faster than Whisper.
- Not for: non-English audio with v2 (`parakeet-tdt-0.6b-v3` covers 25 European languages). Less punctuation control than Whisper.
```python
from parakeet_mlx import from_pretrained
model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")
result = model.transcribe(path)
# result.sentences: each has .start, .text
```
Long files: use `model.transcribe(path, chunk_duration=120, overlap_duration=15)` to limit memory.

##### NeMo (Parakeet / Canary)
- `pip install "nemo_toolkit[asr]"`
- Best for: a Linux server with an NVIDIA GPU. Canary models add translation.
- Not for: Mac. The install is large and has no Apple GPU support.
```python
import nemo.collections.asr as nemo_asr
model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v2")
out = model.transcribe([path], timestamps=True)
# out[0].timestamp["segment"]: each has "start", "segment"
```

##### pywhispercpp (whisper.cpp)
- `pip install pywhispercpp`
- Best for: small install with no PyTorch, Metal GPU on Mac, quantized models.
- Not for: stable API. The binding changes more often than faster-whisper.
```python
from pywhispercpp.model import Model
model = Model("large-v3-turbo", n_threads=8)
segments = model.transcribe(path, language="en")
# segment.t0 is in units of 10 ms: start = segment.t0 / 100; segment.text
```

##### openai-whisper
- `pip install openai-whisper` (needs PyTorch and ffmpeg)
- Best for: the reference implementation, to compare results.
- Not for: production speed. Slowest option on CPU. MPS support is unreliable.
```python
import whisper
model = whisper.load_model("turbo")
result = model.transcribe(path, language="en")
# result["segments"]: each has "start", "text"
```

##### transformers pipeline
- `pip install transformers torch`
- Best for: trying many models (Whisper, distil-whisper, Moonshine) with one API.
- Not for: a light install. Long-form timestamps depend on chunk settings.
```python
from transformers import pipeline
asr = pipeline("automatic-speech-recognition", model="openai/whisper-large-v3-turbo", device="mps")
result = asr(path, return_timestamps=True, chunk_length_s=30, generate_kwargs={"language": "en"})
# result["chunks"]: each has "timestamp" (start, end), "text"
```

##### WhisperX
- `pip install whisperx`
- Best for: speaker labels (diarization) and exact word timestamps. Built on faster-whisper.
- Not for: Mac speed (same CPU limit as faster-whisper). Diarization needs a Hugging Face token and pyannote model terms accepted.
```python
import whisperx
model = whisperx.load_model("large-v3-turbo", device="cpu", compute_type="int8", language="en")
audio = whisperx.load_audio(path)
result = model.transcribe(audio, batch_size=8)
# result["segments"]: each has "start", "text"
```

##### OpenAI API
- `pip install openai`, env `OPENAI_API_KEY`
- Best for: no local compute.
- Not for: files over 25 MB (split first), private audio. Only `whisper-1` returns segment timestamps; `gpt-4o-transcribe` and `gpt-4o-mini-transcribe` return text only.
```python
from openai import OpenAI
client = OpenAI()
with open(path, "rb") as f:
    result = client.audio.transcriptions.create(
        model="whisper-1", file=f, language="en",
        response_format="verbose_json", timestamp_granularities=["segment"],
    )
# result.segments: each has .start, .text
```

##### Groq API
- `pip install groq`, env `GROQ_API_KEY`
- Best for: cloud Whisper `large-v3-turbo` at very high speed and low price. Same request shape as OpenAI.
- Not for: large files without splitting, private audio.
```python
from groq import Groq
client = Groq()
with open(path, "rb") as f:
    result = client.audio.transcriptions.create(
        model="whisper-large-v3-turbo", file=f, language="en", response_format="verbose_json",
    )
# result.segments: each has "start", "text"
```

##### Deepgram / AssemblyAI
- `pip install deepgram-sdk` (model `nova-3`) or `pip install assemblyai` (env `ASSEMBLYAI_API_KEY`)
- Best for: speaker labels, long files with no size limit (upload or URL), extra features (summaries, entity detection).
- Not for: free or offline use. SDK APIs change between major versions; check the vendor docs for the call.
```python
import assemblyai as aai
transcript = aai.Transcriber().transcribe(path, config=aai.TranscriptionConfig(speaker_labels=True, language_code="en"))
# transcript.utterances: each has .start (ms), .speaker, .text
```

### chunk / write
- Chunks are fixed time windows, aligned to multiples of the window length. Empty windows are dropped, but chunk file names keep the real window times.
- `write_chunks` deletes old `chunk_*.txt` files in the target folder first. This prevents files from an earlier run with a different `--chunk-minutes` from remaining.
