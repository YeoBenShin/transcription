"""python -m desktop.selfcheck: import every engine and audio library the app needs, and build the main
window off screen. Exits with 1 and the error if one fails. Used by packaging/build.py --smoke."""

import importlib
import os
import sys

MODULES = ["faster_whisper", "ctranslate2", "onnxruntime", "av", "soxr", "sounddevice", "soundfile", "numpy",
           "yt_dlp", "youtube_transcript_api", "httpx", "live.cli", "live.recorder", "live.transcriber", "app.cli",
           "app.batch", "transcript_merge.cli", "PySide6.QtWidgets", "PySide6.QtSvg"]
if sys.platform == "darwin":
    MODULES += ["mlx.core", "parakeet_mlx"]
if sys.platform == "win32":
    MODULES += ["pyaudiowpatch"]


def main() -> int:
    failed = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as error:  # report every failure, not only the first
            failed.append(f"{name}: {error!r}")
    try:
        from faster_whisper.vad import get_vad_model

        get_vad_model()
    except Exception as error:
        failed.append(f"silero vad: {error!r}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from desktop.settings import Settings
        from desktop.ui.main_window import MainWindow
        from desktop.ui.models_view import DownloadManager
        from desktop import paths

        app = QApplication.instance() or QApplication([])
        window = MainWindow(Settings(), lambda: None, DownloadManager(paths.models_dir()))
        window.show()
        app.processEvents()
        window.close()
    except Exception as error:
        failed.append(f"main window: {error!r}")
    if sys.platform == "darwin":
        from live.recorder import tap_command

        if not os.path.exists(tap_command()[0]):
            failed.append(f"system audio helper missing: {tap_command()[0]}")
    for line in failed:
        print("FAILED", line)
    print("selfcheck:", "failed" if failed else "ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
