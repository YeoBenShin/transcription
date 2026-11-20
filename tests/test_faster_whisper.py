"""The faster-whisper backend with the tiny model on spoken audio."""

from pathlib import Path

import soundfile

from live.backends.faster_whisper import FasterWhisper

SPEECH = Path(__file__).parent / "fixtures" / "speech_22s.flac"


def test_long_span_with_full_prompt():
    audio, _ = soundfile.read(SPEECH, dtype="float32")
    prompt = " ".join(["We discussed the quarterly revenue numbers and the hiring plan for next year."] * 20)
    segments = FasterWhisper("tiny").transcribe(audio, prompt)
    assert "meeting" in " ".join(s.text for s in segments).lower()
