"""Silero VAD (from faster-whisper): where the speech in a span ends."""

from typing import Optional

import numpy as np

from live.backends.base import SAMPLE_RATE

PAUSE_SECONDS = 0.5  # silence this long after speech ends the speech


def speech_end(audio: np.ndarray) -> Optional[float]:
    """Seconds from the start of `audio` where the last speech ends, or None if there is no speech.

    The speech ends only if PAUSE_SECONDS or more of silence follow it. If the speech goes on to
    the end of `audio`, or the silence after it is shorter, the result is len(audio) in seconds.
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(min_silence_duration_ms=int(PAUSE_SECONDS * 1000), speech_pad_ms=0)
    chunks = get_speech_timestamps(audio, options, sampling_rate=SAMPLE_RATE)
    return chunks[-1]["end"] / SAMPLE_RATE if chunks else None


def warm_up() -> None:
    """Load the VAD model."""
    speech_end(np.zeros(SAMPLE_RATE, dtype=np.float32))
