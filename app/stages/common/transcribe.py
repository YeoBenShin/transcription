"""Stage: local speech-to-text with faster-whisper. Progress is saved per segment and resumed."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from app.models import TARGET_LANGUAGE, Line, clean, timestamp

SAMPLE_RATE = 16000  # faster-whisper decodes all audio to 16 kHz mono
PARTIAL_NAME = "full.partial.jsonl"
PROMPT_LINES = 5
MIN_REMAINING_SECONDS = 0.5


@lru_cache(maxsize=1)
def load_model(model_size: str):
    """Load a faster-whisper model once per process and size."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def decode(audio: Path):
    """Decode an audio or video file to a 16 kHz mono float32 array."""
    from faster_whisper import decode_audio

    return decode_audio(str(audio), sampling_rate=SAMPLE_RATE)


def make_header(model_size: str, source_id: str, audio: Path, samples: int) -> dict:
    return {
        "model": model_size,
        "source_id": source_id,
        "audio_bytes": audio.stat().st_size,
        "audio_samples": samples,
    }


def load_partial(partial: Path, header: dict) -> Tuple[List[Line], List[float]]:
    """Return the saved lines and the end time of each one.

    Lines that are not valid JSON (a write cut short by a crash) are skipped.
    If the file is missing or its header is not equal to `header`, return no lines.
    """
    if not partial.is_file():
        return [], []
    records = []
    for raw in partial.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if not records or records[0] != {"header": header}:
        if records:
            print(f"{partial.name} is for other audio or another model; starting again")
        return [], []
    return [Line(r["start"], r["text"]) for r in records[1:]], [r["end"] for r in records[1:]]


def start_partial(partial: Path, header: dict, lines: List[Line], ends: List[float]):
    """Write header and lines to partial (temp file + rename), then return it open for append."""
    tmp = partial.with_name(partial.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"header": header}) + "\n")
        for line, end in zip(lines, ends):
            handle.write(json.dumps({"start": line.start, "end": end, "text": line.text}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, partial)
    return partial.open("a", encoding="utf-8")


def transcribe(audio: Path, model_size: str, workdir: Path, source_id: str) -> List[Line]:
    """Transcribe an audio or video file into timestamped lines.

    Each segment is appended to workdir/full.partial.jsonl as it is made. If that file already
    has lines for the same model, source_id and audio, transcription continues from the end of
    the last saved line, with the last PROMPT_LINES lines as initial_prompt.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    partial = workdir / PARTIAL_NAME
    samples = decode(audio)
    header = make_header(model_size, source_id, audio, len(samples))

    lines, ends = load_partial(partial, header)
    offset = ends[-1] if ends else 0.0
    prompt: Optional[str] = None
    if lines:
        prompt = " ".join(l.text for l in lines[-PROMPT_LINES:])
        print(f"resuming at {timestamp(offset)} ({len(lines)} lines in {partial.name})")

    remaining = samples[int(offset * SAMPLE_RATE):]
    with start_partial(partial, header, lines, ends) as handle:
        if len(remaining) < MIN_REMAINING_SECONDS * SAMPLE_RATE:
            return lines
        print(f"transcribing with faster-whisper ({model_size}, cpu/int8)")
        model = load_model(model_size)
        segments, _ = model.transcribe(
            remaining, language=TARGET_LANGUAGE, vad_filter=True, initial_prompt=prompt
        )
        for segment in segments:
            text = clean(segment.text)
            if not text:
                continue
            line = Line(segment.start + offset, text)
            handle.write(json.dumps({"start": line.start, "end": segment.end + offset, "text": text}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            lines.append(line)
            print(f"  [{timestamp(line.start)}] {text[:70]}", end="\r", flush=True)
    print()
    return lines
