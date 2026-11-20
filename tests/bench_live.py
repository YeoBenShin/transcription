"""Live transcription benchmark: play an audio file into a session in real time, transcribe it live.

python -m tests.bench_live [--audio FILE] [--reference FILE] [--model base] [--out DIR]
pytest -m bench   (tests/test_live_bench.py: the same run on the default audio, with limits)

A writer thread appends the audio to a run file in blocks, at the pace of the audio. The
transcriber `run` reads the same session at the same time. When the audio ends, the session
is marked stopped and the transcriber finishes. Prints:
- WER: word error rate of the transcript against the reference transcript (`app` format).
- write delay: wall time a word was written - wall time its audio was on disk, per written word.
- line delay: the same, for the word that closes a line.
- end lag: time from the audio end to the transcriber end.
- passes: count, time per pass, real-time factor.
The session folder (under --out) keeps the transcript and passes.jsonl: one line per pass with
the span, the VAD speech end, the words of the pass and the records written.
"""

import argparse
import json
import re
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
import soundfile

from live import transcriber, vad
from live.backends import make
from live.progress import Progress
from live.session import PCM_DTYPE, Session, run_name

FIXTURES = Path(__file__).parent / "fixtures"
DEFAULT_AUDIO = FIXTURES / "20260801-Amir’s Lab-Linus Torvalds Explains What Makes Linux So Fast.flac"
BLOCK_SECONDS = 0.1
FILLERS = {"uh", "um", "ah", "er", "hmm", "mm"}


def words_of(text: str) -> List[str]:
    """Lower case words, letters and digits only. Hyphens split words. Fillers are left out."""
    text = text.lower().replace("’", "'").replace("'", "")
    return [w for w in re.split(r"[^a-z0-9]+", text) if w and w not in FILLERS]


def transcript_words(path: Path) -> List[str]:
    """Words of an `app` format transcript: line 1 header, then '[HH:MM:SS] text' lines."""
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    text = " ".join(re.sub(r"^\[\d\d:\d\d:\d\d\]\s*", "", line).replace(">>", " ") for line in lines)
    return words_of(text)


def wer(reference: List[str], hypothesis: List[str]) -> float:
    """Word error rate: (substitutions + deletions + insertions) / reference length."""
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        row = [i] + [0] * len(hypothesis)
        for j, hyp in enumerate(hypothesis, 1):
            row[j] = min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ref != hyp))
        previous = row
    return previous[-1] / max(len(reference), 1)


def stats(values: List[float]) -> dict:
    """{median, p90, max} of values in seconds, rounded to 0.01. All 0 if there are no values."""
    q = sorted(float(v) for v in values) or [0.0]
    return {"median": round(statistics.median(q), 2), "p90": round(q[int(0.9 * (len(q) - 1))], 2), "max": round(q[-1], 2)}


def feed(session: Session, data: np.ndarray, rate: int, t0: float) -> None:
    """Append `data` to one run file in BLOCK_SECONDS blocks, each when its audio has played."""
    path = session.audio_dir / run_name(1, datetime.fromtimestamp(t0), rate, data.shape[1])
    block = int(BLOCK_SECONDS * rate)
    with open(path, "ab") as handle:
        for start in range(0, len(data), block):
            chunk = data[start:start + block]
            wait = t0 + (start + len(chunk)) / rate - time.time()
            if wait > 0:
                time.sleep(wait)
            handle.write(chunk.astype(PCM_DTYPE).tobytes())
            handle.flush()


def bench(audio: Path, reference: Path, model: str, out: Path) -> dict:
    data, rate = soundfile.read(audio, dtype="float32", always_2d=True)
    seconds = len(data) / rate
    session = Session.create(out, "bench")
    backend = make("faster-whisper", model)
    backend.transcribe(np.zeros(16000, dtype=np.float32), None)  # load the model before the clock starts
    vad.warm_up()

    pass_times: List[float] = []
    writes: List[tuple] = []  # (wall time, record)
    inner = backend.transcribe

    def timed(samples, prompt):
        begin = time.time()
        result = inner(samples, prompt)
        pass_times.append(time.time() - begin)
        return result

    backend.transcribe = timed
    t0 = time.time() + 0.5
    passes: List[dict] = []
    original_step = transcriber.Agreement.step

    def step(self, segments, span_start, span_end, final, capped, speech_end):
        heard_before = [w.text for w in self.agreed], [w.text for w in self.tail]
        records, caption = original_step(self, segments, span_start, span_end, final, capped, speech_end)
        passes.append({
            "wall": round(time.time() - t0, 2), "span": [round(span_start, 2), round(span_end, 2)],
            "speech_end": None if speech_end is None else round(speech_end, 2), "final": final, "capped": capped,
            "pass_time": round(pass_times[-1], 2), "before": heard_before,
            "words": " ".join(w.text for s in segments for w in s.words), "records": records,
        })
        return records, caption

    transcriber.Agreement.step = step
    original_append = Progress.append

    def append(self, records):
        now = time.time()
        writes.extend((now, r) for r in records)
        original_append(self, records)

    Progress.append = append
    writer = threading.Thread(target=feed, args=(session, data, rate, t0))
    writer.start()
    try:
        def stop_when_fed():
            writer.join()
            session.mark_stopped()

        threading.Thread(target=stop_when_fed, daemon=True).start()
        transcriber.run(session, backend)
        end_lag = time.time() - (t0 + seconds)
    finally:
        Progress.append = original_append
        transcriber.Agreement.step = original_step
        writer.join()
    with (session.folder / "passes.jsonl").open("w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(p) + "\n" for p in passes)

    word_delays, line_delays = [], []
    for wall, record in writes:
        if record["text"]:
            delay = wall - (t0 + record["end"])
            word_delays += [delay] * len(record["text"].split())
            if not record.get("open"):
                line_delays.append(delay)
    hypothesis = transcript_words(session.transcript)
    reference_words = transcript_words(reference)
    result = {
        "audio": audio.name,
        "model": model,
        "seconds": round(seconds, 1),
        "wer": round(wer(reference_words, hypothesis), 4),
        "reference_words": len(reference_words),
        "hypothesis_words": len(hypothesis),
        "write_delay": stats(word_delays),
        "line_delay": stats(line_delays),
        "end_lag": round(end_lag, 2),
        "passes": len(pass_times),
        "pass_time": stats(pass_times),
        "rtf": round(sum(pass_times) / seconds, 3),
        "transcript": str(session.transcript),
    }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.bench_live")
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--reference", type=Path, help="default: the audio path with .txt")
    parser.add_argument("--model", default="base")
    parser.add_argument("--out", type=Path, default=Path("out") / "bench")
    args = parser.parse_args(argv)
    result = bench(args.audio, args.reference or args.audio.with_suffix(".txt"), args.model, args.out)
    for name, value in result.items():
        print(f"{name:18} {value}")
    print(json.dumps(result), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
