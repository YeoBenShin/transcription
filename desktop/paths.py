"""Per-user folders of the desktop app."""

import os
import sys
from pathlib import Path

APP_NAME = "Transcription"


def data_dir() -> Path:
    """Folder for models and settings: Application Support on macOS, LOCALAPPDATA on Windows."""
    override = os.environ.get("TRANSCRIPTION_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def models_dir() -> Path:
    return data_dir() / "models"


def default_output_dir() -> Path:
    """Default folder for transcripts and recordings."""
    return Path.home() / "Documents" / APP_NAME
