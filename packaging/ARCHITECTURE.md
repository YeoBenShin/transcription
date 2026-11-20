# packaging/

## How it works

- `build.py`: on macOS builds `live/native` first, then runs PyInstaller with `transcription.spec`, then zips the result (`ditto` on macOS, so the bundle signature and links stay). `--smoke`: `transcription-worker -m desktop.selfcheck` (every engine imports, Silero VAD loads, the window builds off screen, the tap helper is in the bundle), then a real transcription of `tests/fixtures/speech_22s.flac` with Whisper tiny by the packaged worker.
- `transcription.spec`: one Analysis of `desktop/entry.py`, two executables in one folder: `Transcription` (no console) and `transcription-worker` (console). `collect_all` for the packages with data or native libraries (faster-whisper with its VAD model, ctranslate2, onnxruntime, av, soxr, PortAudio, libsndfile, tokenizers, yt-dlp, mlx and parakeet-mlx on macOS, pyaudiowpatch on Windows). On macOS a `.app` with `NSMicrophoneUsageDescription` and `NSAudioCaptureUsageDescription`.
- `.github/workflows/desktop.yml`: on `macos-latest` (Apple Silicon) and `windows-latest`: install `requirements-dev.txt`, tests, `build.py --smoke`, upload the zip.

## Decisions

- PyInstaller one-folder, not one-file: a one-file app unpacks about 700 MB at each start.
- The worker executable: see `desktop/ARCHITECTURE.md`, "Two executables in the packaged app".
- Not signed. Gatekeeper and SmartScreen warn at the first start. Signing needs an Apple Developer ID and a Windows certificate.
- The macOS app is ad hoc signed by PyInstaller. macOS ties the System Audio Recording and Microphone permissions to the signature, so a new build asks again.
- Each OS builds its own app; PyInstaller does not cross-build. CI gives the Windows build.
- Size on macOS: about 740 MB unpacked, 290 MB zipped. Most is MLX, librosa's numba and scipy (parakeet-mlx imports librosa for the mel filters), onnxruntime and Qt.
- `PySide6-Essentials` only, not `PySide6`: the add-ons (WebEngine and others) are not used and are large.
