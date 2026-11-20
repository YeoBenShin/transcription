"""macOS System Audio Recording permission, through the live/native helper."""

import subprocess
import sys

from live.recorder import tap_command

GRANTED, DENIED, UNDETERMINED, UNKNOWN = "granted", "denied", "undetermined", "unknown"
SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture"


def _helper(argument: str, timeout: float) -> str:
    if sys.platform != "darwin":
        return GRANTED
    try:
        result = subprocess.run(tap_command() + [argument], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN
    return result.stdout.strip() or UNKNOWN


def system_audio_status() -> str:
    return _helper("--status", 10)


def request_system_audio() -> str:
    """Show the macOS prompt if the user did not answer it before. Blocks until the user answers."""
    return _helper("--request", 600)
