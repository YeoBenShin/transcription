"""Transcriber process: new snippets in the session folder -> transcript lines.

python -m live.transcriber <session folder> [--backend faster-whisper] [--model base]
Polls the session folder. Exits when the session is stopped and all audio is done, or when
stdin closes (end of file).
"""

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.models import Line, timestamp
from app.stages.common.write import write_full
from live import backends
from live.audio import read_16k
from live.backends import Backend, Segment
from live.progress import Progress
from live.session import SOURCE_ID, AlreadyRunning, Session, Timeline, claim, release

POLL_SECONDS = 0.5
MIN_PASS_SECONDS = 1.0  # less new audio than this waits for the next snippet
MAX_PASS_SECONDS = 30.0  # longest span given to the backend in one pass
MIN_FINAL_SECONDS = 0.3  # a stopped session with less audio left than this is done
TAIL_GUARD_SECONDS = 1.5  # a last segment that ends this far before the span end is complete
SILENCE_KEEP_SECONDS = 2.0  # with no speech, this much of the span end is done again next pass
PROMPT_LINES = 5


def choose(
    segments: List[Segment], span_start: float, span_end: float, final: bool, capped: bool
) -> List[dict]:
    """Return the progress records to commit from one pass over [span_start, span_end).

    final: commit all segments, and mark the audio done up to span_end.
    Else commit all segments except the last. The last is also committed if it ends
    TAIL_GUARD_SECONDS or more before span_end, or if the span was cut at MAX_PASS_SECONDS
    and it is the only segment. With no segment at all, mark the audio done up to
    span_end - SILENCE_KEEP_SECONDS.
    Segment times are moved to timeline time. A record with text "" marks audio done with no speech.
    """
    placed = [Segment(span_start + s.start, min(span_start + s.end, span_end), s.text) for s in segments]
    if final:
        commit, end = placed, span_end
    else:
        commit = placed[:-1]
        if placed and (placed[-1].end <= span_end - TAIL_GUARD_SECONDS or (capped and len(placed) == 1)):
            commit = placed
        if commit:
            end = commit[-1].end
        elif placed:
            end = span_start
        else:
            end = span_end - SILENCE_KEEP_SECONDS
    records = [{"start": s.start, "end": s.end, "text": s.text} for s in commit if s.text]
    done = records[-1]["end"] if records else span_start
    if end > done:
        records.append({"start": done, "end": end, "text": ""})
    return records


def plan_span(timeline: Timeline, span_start: float, stopped: bool) -> Tuple[float, bool, bool]:
    """Return (span_end, final, capped) for the next pass.

    The span is at most MAX_PASS_SECONDS (capped) and ends at the next recording gap. Audio
    before a gap is complete, so a span that ends at a gap is final, as is the last span of a
    stopped session.
    """
    span_end = min(timeline.total, span_start + MAX_PASS_SECONDS)
    for gap in timeline.gaps():
        if span_start < gap.at <= span_end:
            return gap.at, True, False
    capped = span_end < timeline.total
    return span_end, stopped and not capped, capped


def run_start(timeline: Timeline, at: float) -> float:
    """Audio time where the recording run that contains `at` starts: the last gap at or before it, or 0."""
    return max((g.at for g in timeline.gaps() if g.at <= at), default=0.0)


def transcript_lines(progress: Progress, timeline: Timeline) -> List[Line]:
    """Progress lines plus one '(recording gap HH:MM:SS)' line per gap, in time order."""
    gaps = [Line(g.at, f"(recording gap {timestamp(g.seconds)})") for g in timeline.gaps()]
    return sorted(progress.lines() + gaps, key=lambda line: line.start)


def header(session: Session, backend: Backend) -> dict:
    return {"session": session.name, "backend": backend.name, "model": backend.model}


def run(
    session: Session,
    backend: Backend,
    should_exit: Callable[[], bool] = lambda: False,
    poll: float = POLL_SECONDS,
) -> bool:
    """Transcribe the session until it is stopped and all audio is done. Return True then.

    Return False if should_exit() becomes True first. The progress file is kept, so a next
    run continues from it.
    """
    progress = Progress(session.partial, header(session, backend))
    if progress.header != header(session, backend):
        print(f"note: earlier lines were made with {progress.header}; new lines use {header(session, backend)}")
    last_total: Optional[float] = None
    gaps_shown = len(session.timeline().gaps())
    try:
        while True:
            stopped = session.is_stopped()
            timeline = session.timeline()
            pending = timeline.total - progress.end
            if stopped and pending < MIN_FINAL_SECONDS:
                break
            if should_exit():
                return False
            fresh = timeline.total != last_total or pending > MAX_PASS_SECONDS
            if not stopped and (not fresh or pending < MIN_PASS_SECONDS):
                time.sleep(poll)
                continue
            last_total = timeline.total
            span_start = progress.end
            span_end, final, capped = plan_span(timeline, span_start, stopped)
            prompt = progress.prompt(PROMPT_LINES, since=run_start(timeline, span_start))
            segments = backend.transcribe(read_16k(timeline, span_start, span_end), prompt)
            records = choose(segments, span_start, span_end, final=final, capped=capped)
            if not records:
                continue
            progress.append(records)
            write_full(transcript_lines(progress, timeline), session.name, SOURCE_ID, session.folder)
            gaps_shown = show_new(records, timeline, gaps_shown)
        write_full(transcript_lines(progress, session.timeline()), session.name, SOURCE_ID, session.folder)
        return True
    finally:
        progress.close()


def show_new(records: List[dict], timeline: Timeline, gaps_shown: int) -> int:
    """Print new gap lines and the committed text lines. Return the number of gaps shown."""
    gaps = timeline.gaps()
    for gap in gaps[gaps_shown:]:
        print(f"[{timestamp(gap.at)}] (recording gap {timestamp(gap.seconds)})", flush=True)
    for record in records:
        if record["text"]:
            print(f"[{timestamp(record['start'])}] {record['text']}", flush=True)
    return len(gaps)


def stdin_closed() -> Callable[[], bool]:
    """Return a function that is True after stdin reaches end of file."""
    from live.recorder import wait_for_stdin_close

    closed = threading.Event()
    threading.Thread(target=wait_for_stdin_close, args=(closed,), daemon=True).start()
    return closed.is_set


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m live.transcriber")
    parser.add_argument("session", type=Path)
    parser.add_argument("--backend", default=backends.DEFAULT_BACKEND, choices=backends.names())
    parser.add_argument("--model", default=backends.DEFAULT_MODEL)
    args = parser.parse_args(argv)

    session = Session(args.session)
    try:
        claim(session, "transcriber")
    except AlreadyRunning as error:
        print(error, flush=True)
        return 1
    try:
        done = run(session, backends.make(args.backend, args.model), should_exit=stdin_closed())
    finally:
        release(session, "transcriber")
    return 0 if done else 2


if __name__ == "__main__":
    sys.exit(main())
