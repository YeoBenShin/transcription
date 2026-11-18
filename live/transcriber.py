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

from app.models import Line, timestamp
from app.stages.common.write import write_full
from live import backends, console
from live.console import Caption
from live.audio import read_16k
from live.backends import Backend, Segment, Word
from live.progress import Progress
from live.session import SOURCE_ID, AlreadyRunning, Session, Timeline, claim, release

POLL_SECONDS = 0.2
MIN_PASS_SECONDS = 1.0  # less pending audio than this waits
STEP_SECONDS = 1.0  # a live pass runs when the timeline grew this much since the last pass
MAX_PASS_SECONDS = 30.0  # longest span given to the backend in one pass
MIN_FINAL_SECONDS = 0.3  # a stopped session with less audio left than this is done
TAIL_GUARD_SECONDS = 1.5  # speech that ends this far before the span end is complete
SILENCE_KEEP_SECONDS = 2.0  # with no speech, this much of the span end is done again next pass
LINE_MAX_SECONDS = 8.0  # agreed words this long with no sentence end are written up to a clause end
OVERLAP_SECONDS = 0.1  # a word that starts this close before the last agreed word end is a new word
OVERLAP_WORDS = 5  # longest run of agreed words that a pass can repeat at its start
SENTENCE_END = (".", "?", "!")
CLAUSE_END = (",", ";", ":")
PROMPT_LINES = 5


def key(word: Word) -> str:
    """Word text in lower case, letters and digits only. Two passes agree on a word if keys are equal."""
    return "".join(c for c in word.text.lower() if c.isalnum())


def place(segments: List[Segment], span_start: float, span_end: float) -> List[Word]:
    """The words of all segments in timeline time, the end cut at span_end. Words with no text are left out."""
    return [
        Word(span_start + w.start, min(span_start + w.end, span_end), w.text)
        for s in segments
        for w in s.words
        if w.text
    ]


def common_prefix(a: List[Word], b: List[Word]) -> int:
    """Number of words at the start of a and b with equal keys."""
    count = 0
    for x, y in zip(a, b):
        if key(x) != key(y):
            break
        count += 1
    return count


def last_end(words: List[Word], marks: Tuple[str, ...]) -> int:
    """Number of words up to and including the last word that ends with one of `marks`, or 0."""
    return max((i + 1 for i, w in enumerate(words) if w.text.endswith(marks)), default=0)


def to_lines(words: List[Word]) -> List[dict]:
    """Progress records, one per sentence. A line ends at a word that ends with SENTENCE_END, or at the last word."""
    records, line = [], []
    for word in words:
        line.append(word)
        if word.text.endswith(SENTENCE_END) or word is words[-1]:
            records.append({"start": line[0].start, "end": line[-1].end, "text": " ".join(w.text for w in line)})
            line = []
    return records


class Agreement:
    """The words after the last written line, kept in memory between passes.

    agreed: words that two passes in a row gave, in the same order. They do not change.
    tail: the words after them from the last pass. The next pass can change them.
    """

    def __init__(self):
        self.agreed: List[Word] = []
        self.tail: List[Word] = []

    def new_words(self, words: List[Word]) -> List[Word]:
        """The words of a pass that come after the agreed words.

        A pass starts at the last written line, so it gives the agreed words again. Words that
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
        self, segments: List[Segment], span_start: float, span_end: float, final: bool, capped: bool
    ) -> Tuple[List[dict], Optional[Caption]]:
        """Return (progress records to write, caption to show) from one pass over [span_start, span_end).

        The words of this pass that agree with the tail of the last pass become agreed. Then write:
        - final: all words, and mark the audio done up to span_end.
        - speech ends TAIL_GUARD_SECONDS or more before span_end: all words.
        - capped (span cut at MAX_PASS_SECONDS): all words except the last.
        - else: the agreed words up to the last sentence end. If there is none and the agreed
          words are LINE_MAX_SECONDS or longer, the agreed words up to the last clause end, or
          all agreed words if there is none.
        With no words at all, mark the audio done up to span_end - SILENCE_KEEP_SECONDS.
        A record with text "" marks audio done with no speech. The caption is the words not written.
        """
        new = self.new_words(place(segments, span_start, span_end))
        confirmed = 0 if final else common_prefix(self.tail, new)
        self.agreed += new[:confirmed]
        self.tail = new[confirmed:]
        heard = self.agreed + self.tail
        if final:
            write, end = heard, span_end
        elif not heard:
            write, end = [], span_end - SILENCE_KEEP_SECONDS
        else:
            if heard[-1].end <= span_end - TAIL_GUARD_SECONDS:
                write = heard
            elif capped:
                write = heard[:-1] or heard
            else:
                write = self.agreed[: last_end(self.agreed, SENTENCE_END)]
                if not write and self.agreed and self.agreed[-1].end - self.agreed[0].start >= LINE_MAX_SECONDS:
                    write = self.agreed[: last_end(self.agreed, CLAUSE_END) or len(self.agreed)]
            end = write[-1].end if write else span_start
        records = to_lines(write)
        done = records[-1]["end"] if records else span_start
        if end > done:
            records.append({"start": done, "end": end, "text": ""})
        self.drop(len(write))
        if final:
            self.agreed, self.tail = [], []
        return records, self.caption()

    def drop(self, count: int) -> None:
        """Remove the first `count` words of agreed + tail."""
        if count >= len(self.agreed):
            self.tail = self.tail[count - len(self.agreed):]
            self.agreed = []
        else:
            self.agreed = self.agreed[count:]

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
        console.say(f"note: earlier lines were made with {progress.header}; new lines use {header(session, backend)}")
    last_total: Optional[float] = None
    gaps_shown = len(session.timeline().gaps())
    draft = console.Draft()
    agreement = Agreement()
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
            prompt = progress.prompt(PROMPT_LINES, since=run_start(timeline, span_start))
            segments = backend.transcribe(read_16k(timeline, span_start, span_end), prompt)
            records, held = agreement.step(segments, span_start, span_end, final=final, capped=capped)
            if records:
                progress.append(records)
                write_full(transcript_lines(progress, timeline), session.name, SOURCE_ID, session.folder)
                gaps_shown = show_new(records, timeline, gaps_shown)
            draft.show(held)
        write_full(transcript_lines(progress, session.timeline()), session.name, SOURCE_ID, session.folder)
        return True
    finally:
        draft.clear()
        progress.close()


def show_new(records: List[dict], timeline: Timeline, gaps_shown: int) -> int:
    """Print new gap lines and the committed text lines. Return the number of gaps shown.

    Each line replaces the draft line, if one is on the screen.
    """
    gaps = timeline.gaps()
    for gap in gaps[gaps_shown:]:
        console.say(f"[{timestamp(gap.at)}] (recording gap {timestamp(gap.seconds)})")
    for record in records:
        if record["text"]:
            console.say(f"[{timestamp(record['start'])}] {record['text']}")
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
