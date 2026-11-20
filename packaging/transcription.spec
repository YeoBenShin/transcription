# PyInstaller spec of the desktop app. Build with packaging/build.py, not with pyinstaller directly.
#
# Two executables share one folder: "Transcription" (the window, no console) and "transcription-worker"
# (console) that runs the child processes as `transcription-worker -m <module> ...`.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
APP_NAME = "Transcription"
WORKER_NAME = "transcription-worker"
MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

datas, binaries, hiddenimports = [], [], []
for package in ("app", "live", "transcript_merge", "desktop"):
    hiddenimports += collect_submodules(package)
packages = ["faster_whisper", "ctranslate2", "onnxruntime", "av", "soxr", "_sounddevice_data", "_soundfile_data",
            "tokenizers", "yt_dlp", "youtube_transcript_api", "huggingface_hub", "certifi"]
if MAC:
    packages += ["mlx", "parakeet_mlx"]
if WINDOWS:
    packages += ["pyaudiowpatch"]
for package in packages:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["sounddevice", "soundfile"]
if MAC:
    binaries.append((str(ROOT / "live" / "native" / "bin" / "system-audio-tap"), "."))

analysis = Analysis(
    [str(ROOT / "desktop" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

gui = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=APP_NAME, console=False,
          argv_emulation=False, upx=False)
worker = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name=WORKER_NAME, console=True, upx=False)
folder = COLLECT(gui, worker, analysis.binaries, analysis.datas, name=APP_NAME, upx=False)

if MAC:
    app = BUNDLE(
        folder,
        name=f"{APP_NAME}.app",
        bundle_identifier="io.github.transcription.desktop",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Records your voice for the live transcript.",
            "NSAudioCaptureUsageDescription": "Records the sound of other apps (for example a meeting) for the live "
                                              "transcript.",
        },
    )
