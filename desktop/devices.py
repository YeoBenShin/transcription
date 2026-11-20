"""Audio devices for the Recording settings: input devices (sounddevice) and, on Windows, playback devices
that can be recorded by WASAPI loopback (pyaudiowpatch)."""

import sys
from typing import List


def input_devices() -> List[str]:
    """Names of the input devices, read again from the OS each call."""
    try:
        import sounddevice

        sounddevice._terminate()
        sounddevice._initialize()
        return [d["name"] for d in sounddevice.query_devices() if d["max_input_channels"] > 0]
    except Exception:
        return []


def loopback_devices() -> List[str]:
    """Windows: playback device names (without the ' [Loopback]' suffix). Empty on other systems."""
    if sys.platform != "win32":
        return []
    try:
        import pyaudiowpatch

        from live.recorder import loopback_devices as find

        audio = pyaudiowpatch.PyAudio()
        try:
            return [d["name"].replace("[Loopback]", "").strip() for d in find(audio)]
        finally:
            audio.terminate()
    except Exception:
        return []
