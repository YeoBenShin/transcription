# .config

## How it works

- `configuration.winget` is a WinGet Configuration (DSC) file. `winget configure -f .config/configuration.winget` applies it.
- `WinGetPackage` resources install the machine packages. The `PSDscResources/Script` resource then creates `.venv` in the project folder (`${WinGetConfigRoot}\..`) and runs `pip install -r requirements.txt`.
- Each resource tests its state first. A second run changes only what is missing.

## Decisions

- Python 3.13: same as `.python-version`. `onnxruntime` has no wheels for 3.15.
- Visual C++ runtime: the `ctranslate2`, `onnxruntime` and `av` wheels load MSVC DLLs. A clean Windows machine can be without them.
- Deno: recent yt-dlp versions need an external JavaScript runtime for full YouTube support. Without it, some audio downloads fail.
- No ffmpeg: yt-dlp saves the audio stream with no re-encoding and faster-whisper decodes through PyAV.
- No CUDA: `app/stages/common/transcribe.py` runs on CPU (`device="cpu"`).
- The Script step reloads `Path` from the registry, because the Python install does not update the `Path` of the running process. It calls `py -3.13` so that a different default Python on the machine is not used.
