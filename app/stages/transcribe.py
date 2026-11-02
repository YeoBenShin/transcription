"""Stage: local speech-to-text with faster-whisper."""

from pathlib import Path
from typing import List

from app.models import TARGET_LANGUAGE, Line, clean, timestamp


def transcribe(audio: Path, model_size: str) -> List[Line]:
    """Transcribe an audio or video file into timestamped lines."""
    from faster_whisper import WhisperModel

    print(f"transcribing with faster-whisper ({model_size}, cpu/int8)")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language=TARGET_LANGUAGE, vad_filter=True)
    lines = []
    for segment in segments:
        text = clean(segment.text)
        if text:
            lines.append(Line(segment.start, text))
            print(f"  [{timestamp(segment.start)}] {text[:70]}", end="\r", flush=True)
    print()
    return lines
