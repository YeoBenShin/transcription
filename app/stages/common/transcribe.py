"""Stage: local speech-to-text with faster-whisper. Progress is saved per segment and resumed."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from app.models import TARGET_LANGUAGE, Line, clean, timestamp

SAMPLE_RATE = 16000  # faster-whisper decodes all audio to 16 kHz mono
PARTIAL_SUFFIX = ".partial.jsonl"
PROMPT_LINES = 5
MIN_REMAINING_SECONDS = 0.5


@lru_cache(maxsize=1)
def load_model(model_size: str):
    """Load a faster-whisper model once per process and size."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def partial_path(temp_dir: Path, name: str) -> Path:
    """Return the progress file path for output name `name`: temp_dir/<name>.partial.jsonl."""
    return temp_dir / f"{name}{PARTIAL_SUFFIX}"


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


def complete_length(data: bytes) -> int:
    """Return the byte length of data up to and including its last newline."""
    return data.rfind(b"\n") + 1


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_segment(record) -> bool:
    """Return True if record is a dict with numeric start and end and a str text."""
    return (
        isinstance(record, dict)
        and is_number(record.get("start"))
        and is_number(record.get("end"))
        and isinstance(record.get("text"), str)
    )


def load_partial(partial: Path, header: dict) -> Tuple[List[Line], List[float]]:
    """Return the saved lines and the end time of each one.

    Only lines that end with a newline are read. A last line with no newline is a write
    cut short by a crash, and is ignored. Blank lines are ignored.
    If the file is missing, a complete line is not valid UTF-8 or JSON, a segment line does
    not have numeric start and end and a str text, or the header is not equal to `header`,
    return no lines.
    """
    if not partial.is_file():
        return [], []
    data = partial.read_bytes()
    try:
        text = data[: complete_length(data)].decode("utf-8")
        records = [json.loads(raw) for raw in text.splitlines() if raw.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError):
        print(f"{partial.name} cannot be read; starting again")
        return [], []
    if not records or records[0] != {"header": header}:
        if records:
            print(f"{partial.name} is for other audio or another model; starting again")
        return [], []
    segments = records[1:]
    if not all(is_segment(r) for r in segments):
        print(f"{partial.name} has a line that is not a segment; starting again")
        return [], []
    return [Line(r["start"], r["text"]) for r in segments], [r["end"] for r in segments]


def open_partial(partial: Path, header: dict, resume: bool):
    """Return partial open for append.

    resume=True: cut the file after its last newline, which removes a line cut short by a crash.
    resume=False: replace the file with one that has only the header line.
    """
    if resume:
        with partial.open("r+b") as handle:
            handle.truncate(complete_length(handle.read()))
    else:
        with partial.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"header": header}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return partial.open("a", encoding="utf-8")


def transcribe(audio: Path, model_size: str, partial: Path, source_id: str) -> List[Line]:
    """Transcribe an audio or video file into timestamped lines.

    Each segment is appended to the `partial` progress file as it is made. If that file already
    has lines for the same model, source_id and audio, transcription continues from the end of
    the last saved line, with the last PROMPT_LINES lines as initial_prompt.
    """
    partial.parent.mkdir(parents=True, exist_ok=True)
    samples = decode(audio)
    header = make_header(model_size, source_id, audio, len(samples))

    lines, ends = load_partial(partial, header)
    offset = ends[-1] if ends else 0.0
    prompt: Optional[str] = None
    if lines:
        prompt = " ".join(l.text for l in lines[-PROMPT_LINES:])
        print(f"resuming at {timestamp(offset)} ({len(lines)} lines in {partial.name})")

    remaining = samples[int(offset * SAMPLE_RATE):]
    with open_partial(partial, header, resume=bool(lines)) as handle:
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
