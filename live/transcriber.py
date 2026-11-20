"""Transcriber process: new audio in the run files of all streams of the session -> transcript lines.

python -m live.transcriber <session folder> [--backend faster-whisper] [--model base]
Polls the session folder. Exits when the session is stopped and all audio is done, or when
stdin closes (end of file).
"""

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from app.models import Line, timestamp
from app.stages.common.write import write_full
from live import backends, console
from live.console import Caption
from live.audio import read_16k
from live.backends import Backend, Segment, Word
from live.progress import Progress
from live.session import SOURCE_ID, AlreadyRunning, Session, Stream, Timeline, claim, release

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
GAP_MATCH_SECONDS = 2.0  # a gap of another stream this close to a reference gap is the same stop


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


class StreamState:
    """The transcription state of one stream: its progress file, agreement and the timeline of its last listing."""

    def __init__(self, stream: Stream, backend: Backend):
        self.stream = stream
        self.progress = Progress(stream.partial, header(stream.session, backend))
        if self.progress.header != header(stream.session, backend):
            console.say(
                f"note: earlier {stream.name} lines were made with {self.progress.header}; "
                f"new lines use {header(stream.session, backend)}"
            )
        self.agreement = Agreement()
        self.timeline = stream.timeline()
        self.last_total: Optional[float] = None
        self.caption: Optional[Caption] = None

    def list(self) -> Timeline:
        self.timeline = self.stream.timeline()
        return self.timeline

    def pending(self) -> float:
        return self.timeline.total - self.progress.end

    def due(self, stopped: bool) -> bool:
        """True if a pass must run now on the current timeline.

        Stopped: while MIN_FINAL_SECONDS or more is left. Live: when MIN_PASS_SECONDS or more is
        pending and the timeline grew STEP_SECONDS since the last pass, or more than
        MAX_PASS_SECONDS is pending.
        """
        pending = self.pending()
        if stopped:
            return pending >= MIN_FINAL_SECONDS
        grown = self.last_total is None or self.timeline.total - self.last_total >= STEP_SECONDS
        return pending >= MIN_PASS_SECONDS and (grown or pending > MAX_PASS_SECONDS)

    def step(self, backend: Backend, stopped: bool) -> List[dict]:
        """Run one pass, append the records to the progress file, and return them."""
        timeline = self.timeline
        self.last_total = timeline.total
        span_start = self.progress.end
        span_end, final, capped = plan_span(timeline, span_start, stopped)
        prompt = self.progress.prompt(PROMPT_LINES, since=run_start(timeline, span_start))
        segments = backend.transcribe(read_16k(timeline, span_start, span_end), prompt)
        records, self.caption = self.agreement.step(segments, span_start, span_end, final=final, capped=capped)
        if records:
            self.progress.append(records)
        return records


class Layout:
    """Places the lines of all streams on one time axis: the timeline of the reference stream.

    The reference is the first stream (in STREAMS order) with audio. A time of another stream
    goes to its wall time, then to the reference time at that wall time. With more than one
    stream, each line starts with the stream label.
    """

    def __init__(self, states: List[StreamState]):
        self.states = states
        self.reference = next((s for s in states if s.timeline.files), None)
        self.labelled = len(states) > 1

    def time(self, state: StreamState, at: float) -> float:
        if state is self.reference or self.reference is None or not state.timeline.files:
            return at
        return self.reference.timeline.time_at(state.timeline.wall_at(at))

    def text(self, state: StreamState, text: str) -> str:
        return f"{state.stream.label}: {text}" if self.labelled else text

    def line(self, state: StreamState, record: dict) -> Line:
        return Line(self.time(state, record["start"]), self.text(state, record["text"]))

    def gap_lines(self) -> List[Line]:
        """One '(recording gap HH:MM:SS)' line per gap of the reference stream.

        A gap of another stream gets its own line with its label, unless a reference gap is
        within GAP_MATCH_SECONDS of it (both recorders stopped).
        """
        if self.reference is None:
            return []
        own = [Line(g.at, f"(recording gap {timestamp(g.seconds)})") for g in self.reference.timeline.gaps()]
        result = list(own)
        for state in self.states:
            if state is self.reference:
                continue
            for gap in state.timeline.gaps():
                at = self.time(state, gap.at)
                if all(abs(at - line.start) > GAP_MATCH_SECONDS for line in own):
                    result.append(Line(at, f"({state.stream.label}: recording gap {timestamp(gap.seconds)})"))
        return result

    def lines(self) -> List[Line]:
        """All text lines of all streams and the gap lines, in time order."""
        text = [self.line(s, r) for s in self.states for r in s.progress.records if r["text"]]
        return sorted(text + self.gap_lines(), key=lambda line: line.start)

    def caption(self) -> Optional[Caption]:
        """The caption of the stream whose held words start last, on the reference time axis, with its label."""
        shown = [
            Caption(self.time(s, s.caption.start), s.caption.agreed, s.caption.tail, s.stream.label if self.labelled else "")
            for s in self.states
            if s.caption is not None
        ]
        return max(shown, key=lambda c: c.start, default=None)


def header(session: Session, backend: Backend) -> dict:
    return {"session": session.name, "backend": backend.name, "model": backend.model}


def run(
    session: Session,
    backend: Backend,
    should_exit: Callable[[], bool] = lambda: False,
    poll: float = POLL_SECONDS,
) -> bool:
    """Transcribe all streams of the session until it is stopped and all audio is done. Return True then.

    Each loop gives one pass to each stream that is due. Return False if should_exit() becomes
    True first. The progress files are kept, so a next run continues from them.
    """
    states: Dict[str, StreamState] = {}
    shown_gaps: Set[Tuple[float, str]] = set()
    draft = console.Draft()
    try:
        while True:
            stopped = session.is_stopped()
            for stream in session.streams():
                if stream.name not in states:
                    states[stream.name] = StreamState(stream, backend)
                    shown_gaps |= {(g.start, g.text) for g in Layout(list(states.values())).gap_lines()}
            for state in states.values():
                state.list()
            if stopped and not any(state.due(stopped) for state in states.values()):
                break
            if should_exit():
                return False
            ran = False
            for state in states.values():
                if not state.due(stopped):
                    continue
                records = state.step(backend, stopped)
                ran = True
                layout = Layout(list(states.values()))
                if records:
                    write_full(layout.lines(), session.name, SOURCE_ID, session.folder)
                    shown_gaps = show_new(layout, state, records, shown_gaps)
                draft.show(layout.caption())
            if not ran:
                time.sleep(poll)
        if states:
            write_full(Layout(list(states.values())).lines(), session.name, SOURCE_ID, session.folder)
        return True
    finally:
        draft.clear()
        for state in states.values():
            state.progress.close()


def show_new(layout: Layout, state: StreamState, records: List[dict], shown_gaps: Set[Tuple[float, str]]):
    """Print the gap lines not shown before and the committed text lines of `state`. Return the shown gaps.

    Each line replaces the draft line, if one is on the screen.
    """
    for gap in layout.gap_lines():
        if (gap.start, gap.text) not in shown_gaps:
            console.say(f"[{timestamp(gap.start)}] {gap.text}")
            shown_gaps = shown_gaps | {(gap.start, gap.text)}
    for record in records:
        if record["text"]:
            line = layout.line(state, record)
            console.say(f"[{timestamp(line.start)}] {line.text}")
    return shown_gaps


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
