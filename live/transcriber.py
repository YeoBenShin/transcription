"""Transcriber process: new audio in the run files of the session folder -> transcript lines.

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

import numpy as np

from app.models import Line, timestamp
from app.stages.common.write import write_full
from live import backends, console, loops, vad
from live.console import Caption
from live.audio import read_16k
from live.backends import Backend, Segment, Word
from live.progress import Progress, TextLine
from live.session import SOURCE_ID, AlreadyRunning, Session, Timeline, claim, release

POLL_SECONDS = 0.2
MIN_PASS_SECONDS = 1.0  # less pending audio than this waits
STEP_SECONDS = 1.0  # a live pass runs when the timeline grew this much since the last pass
MAX_PASS_SECONDS = 30.0  # longest span given to the backend in one pass
MIN_FINAL_SECONDS = 0.3  # a stopped session with less audio left than this is done
SILENCE_KEEP_SECONDS = 2.0  # with no speech, this much of the span end is done again next pass
OVERLAP_SECONDS = 0.1  # a word that starts this close before the last agreed word end is a new word
OVERLAP_WORDS = 5  # longest run of agreed words that a pass can repeat at its start
SENTENCE_END = (".", "?", "!")
SENTENCE_GUARD_SECONDS = 1.5  # a sentence end with this much audio after it closes the line


def key(word: Word) -> str:
    """Word text in lower case, letters and digits only. Two passes agree on a word if keys are equal."""
    return loops.key(word.text)


def place(segments: List[Segment], span_start: float, span_end: float) -> List[Word]:
    """The words of all segments in timeline time, the end cut at span_end. Words with no text are
    left out. A segment with a loop is cut where the loop repeats (loops.loop_cut).
    """
    words = []
    for segment in segments:
        items = [w for w in segment.words if w.text]
        cut = loops.loop_cut([w.text for w in items])
        words += [Word(span_start + w.start, min(span_start + w.end, span_end), w.text) for w in items[:cut]]
    return words


def common_prefix(a: List[Word], b: List[Word]) -> int:
    """Number of words at the start of a and b with equal keys."""
    count = 0
    for x, y in zip(a, b):
        if key(x) != key(y):
            break
        count += 1
    return count


def is_sentence_end(word: Word) -> bool:
    return word.text.endswith(SENTENCE_END)


def line_ends(line: List[Word], i: int, heard_to: float) -> bool:
    """True if the line closes after line[i]: it ends with SENTENCE_END, and a word comes after it
    or it ends SENTENCE_GUARD_SECONDS or more before `heard_to`.
    """
    return is_sentence_end(line[i]) and (i < len(line) - 1 or line[i].end <= heard_to - SENTENCE_GUARD_SECONDS)


def pieces(line: List[Word], written: int, close: bool, heard_to: float) -> List[dict]:
    """Progress records for line[written:], the words written to the open line line[:written].

    The line closes where line_ends is True, and after the last word if `close`. A record whose
    line does not close has "open": true. If the line closes right after line[written - 1], a
    record with text "" closes it.
    """
    records, piece = [], []
    if written and line_ends(line, written - 1, heard_to) or close and line and written == len(line):
        end = line[written - 1].end
        records.append({"start": end, "end": end, "text": ""})
    for i in range(written, len(line)):
        piece.append(line[i])
        last = i == len(line) - 1
        closes = (close and last) or line_ends(line, i, heard_to)
        if closes or last:
            record = {"start": piece[0].start, "end": piece[-1].end, "text": " ".join(w.text for w in piece)}
            if not closes:
                record["open"] = True
            records.append(record)
            piece = []
    return records


def open_line(line: List[Word], heard_to: float) -> List[Word]:
    """The words after the last place where the line closes (line_ends)."""
    return line[max((i + 1 for i in range(len(line)) if line_ends(line, i, heard_to)), default=0):]


def open_words(records: List[dict]) -> List[Word]:
    """Words of the open records, with times spread evenly over each record."""
    words = []
    for record in records:
        texts = record["text"].split()
        step = (record["end"] - record["start"]) / max(len(texts), 1)
        words += [
            Word(record["start"] + i * step, record["start"] + (i + 1) * step, text) for i, text in enumerate(texts)
        ]
    return words


class Agreement:
    """The words after the last closed line, kept in memory between passes.

    agreed: words that two passes in a row gave, in the same order. They are written at once
      into the open line, and do not change.
    tail: the words after them from the last pass. The next pass can change them.
    """

    def __init__(self, agreed: Optional[List[Word]] = None):
        self.agreed: List[Word] = list(agreed or [])
        self.tail: List[Word] = []

    def new_words(self, words: List[Word]) -> List[Word]:
        """The words of a pass that come after the agreed words.

        A pass starts at the last closed line, so it gives the agreed words again. Words that
        start before the last agreed word end are removed, then up to OVERLAP_WORDS words that
        repeat the end of the agreed words.
        """
        if not self.agreed:
            return words
        after = self.agreed[-1].end - OVERLAP_SECONDS
        words = [w for w in words if w.start > after]
        for count in range(min(OVERLAP_WORDS, len(self.agreed), len(words)), 0, -1):
            if [key(w) for w in self.agreed[-count:]] == [key(w) for w in words[:count]]:
                return words[count:]
        return words

    def step(
        self,
        segments: List[Segment],
        span_start: float,
        span_end: float,
        final: bool,
        capped: bool,
        speech_end: Optional[float],
    ) -> Tuple[List[dict], Optional[Caption]]:
        """Return (progress records to write, caption to show) from one pass over [span_start, span_end).

        `speech_end` is the timeline time where the VAD found the end of speech (span_end if speech
        goes on, None if there is no speech). The words of this pass that agree with the tail of the
        last pass become agreed. Then write into the open line:
        - final: all words; close the line and mark the audio done up to span_end.
        - speech ends before span_end, or no speech: all words; close the line.
        - capped (span cut at MAX_PASS_SECONDS): all words except the last; close the line.
        - else: the new agreed words. The line closes after a sentence end that has an agreed word
          after it, or SENTENCE_GUARD_SECONDS of audio after it.
        With no words at all, mark the audio done up to span_end - SILENCE_KEEP_SECONDS.
        A record with text "" marks audio done with no speech. The caption is the open line and the tail.
        """
        new = self.new_words(place(segments, span_start, span_end))
        confirmed = 0 if final else common_prefix(self.tail, new)
        fresh = new[:confirmed]
        self.tail = new[confirmed:]
        heard = self.agreed + fresh + self.tail
        close = True
        if final:
            write, self.tail = fresh + self.tail, []
        elif not heard:
            write = []
        elif speech_end is None or speech_end < span_end:
            write, self.tail = fresh + self.tail, []
        elif capped:
            write = (fresh + self.tail)[:-1]
            self.tail = (fresh + self.tail)[-1:]
            if not write and not self.agreed:
                write, self.tail = self.tail, []
        else:
            write, close = fresh, False
        line = self.agreed + write
        records = pieces(line, len(self.agreed), close, span_end)
        done = records[-1]["end"] if records else span_start
        end = span_end if final else span_end - SILENCE_KEEP_SECONDS if not heard else done
        if end > done:
            records.append({"start": done, "end": end, "text": ""})
        self.agreed = [] if close else open_line(line, span_end)
        return records, self.caption()

    def caption(self) -> Optional[Caption]:
        words = self.agreed + self.tail
        if not words:
            return None
        return Caption(
            words[0].start, " ".join(w.text for w in self.agreed), " ".join(w.text for w in self.tail)
        )


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


def span_speech_end(end: Optional[float], samples: np.ndarray, span_start: float, span_end: float) -> Optional[float]:
    """Timeline time of a VAD speech end `end` (seconds from the span start). Speech that goes on
    to the last sample gives span_end. None stays None.
    """
    if end is None:
        return None
    return span_end if end >= len(samples) / backends.SAMPLE_RATE else span_start + end


def header(session: Session, backend: Backend) -> dict:
    return {"session": session.name, "backend": backend.name, "model": backend.model}


def run(
    session: Session,
    backend: Backend,
    should_exit: Callable[[], bool] = lambda: False,
    poll: float = POLL_SECONDS,
    speech_end: Optional[Callable[[np.ndarray], Optional[float]]] = None,
) -> bool:
    """Transcribe the session until it is stopped and all audio is done. Return True then.

    Return False if should_exit() becomes True first. The progress file is kept, so a next
    run continues from it, with the words of its open line as the agreed words.
    `speech_end` gives where the speech in the span audio ends (default vad.speech_end).
    """
    speech_end = speech_end or vad.speech_end
    progress = Progress(session.partial, header(session, backend))
    if progress.header != header(session, backend):
        console.say(f"note: earlier lines were made with {progress.header}; new lines use {header(session, backend)}")
    last_total: Optional[float] = None
    gaps_shown = len(session.timeline().gaps())
    draft = console.Draft()
    agreement = Agreement(open_words(progress.open_records()))
    try:
        while True:
            stopped = session.is_stopped()
            timeline = session.timeline()
            pending = timeline.total - progress.end
            if stopped and pending < MIN_FINAL_SECONDS:
                break
            if should_exit():
                return False
            grown = last_total is None or timeline.total - last_total >= STEP_SECONDS
            fresh = grown or pending > MAX_PASS_SECONDS
            if not stopped and (not fresh or pending < MIN_PASS_SECONDS):
                time.sleep(poll)
                continue
            last_total = timeline.total
            span_start = progress.end
            span_end, final, capped = plan_span(timeline, span_start, stopped)
            prompt = progress.prompt(since=run_start(timeline, span_start), before=span_start)
            samples = read_16k(timeline, span_start, span_end)
            segments = backend.transcribe(samples, prompt)
            records, held = agreement.step(
                segments, span_start, span_end, final=final, capped=capped,
                speech_end=span_speech_end(speech_end(samples), samples, span_start, span_end),
            )
            if records:
                closed = progress.closed_count()
                progress.append(records)
                write_full(transcript_lines(progress, timeline), session.name, SOURCE_ID, session.folder)
                gaps_shown = show_new(progress.text_lines()[closed:], timeline, gaps_shown)
            draft.show(held)
        write_full(transcript_lines(progress, session.timeline()), session.name, SOURCE_ID, session.folder)
        return True
    finally:
        draft.clear()
        progress.close()


def show_new(lines: List[TextLine], timeline: Timeline, gaps_shown: int) -> int:
    """Print new gap lines and the lines that closed. Return the number of gaps shown.

    Each line replaces the draft line, if one is on the screen. An open line shows in the draft.
    """
    gaps = timeline.gaps()
    for gap in gaps[gaps_shown:]:
        console.say(f"[{timestamp(gap.at)}] (recording gap {timestamp(gap.seconds)})")
    for line in lines:
        if line.closed:
            console.say(f"[{timestamp(line.start)}] {line.text}")
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
        console.say(str(error))
        return 1
    try:
        done = run(session, backends.make(args.backend, args.model), should_exit=stdin_closed())
    finally:
        release(session, "transcriber")
    return 0 if done else 2


if __name__ == "__main__":
    sys.exit(main())
