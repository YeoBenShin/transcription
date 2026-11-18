"""live/: run file writing, timeline gaps, commit rules, the transcriber loop, merge and finalize.

No audio device and no Whisper. Run files are 16 kHz mono .pcm files whose sample value is
the timeline time / 1000, so a fake backend can read where its audio comes from.
"""

import os
import queue
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile

from app.models import Line
from live import audio, backends, console, finalize as finalize_module, transcriber
from live.backends import Segment, Word
from live.progress import Progress
from live import recorder
from live.recorder import RunWriter
from live.session import Session, run_name

RATE = 16000
T0 = datetime(2026, 11, 18, 14, 0, 0)


def ramp(start: float, seconds: float) -> np.ndarray:
    """Mono samples whose value is (timeline time / 1000)."""
    return ((start + np.arange(int(seconds * RATE)) / RATE) / 1000).astype(np.float32)


def write_run(session: Session, data: np.ndarray, wall: datetime, rate: int = RATE) -> Path:
    """Write `data` (frames, or frames x channels) as the next run file."""
    data = data if data.ndim == 2 else data[:, None]
    path = session.audio_dir / run_name(session.next_seq(), wall, rate, data.shape[1])
    path.write_bytes(data.astype("<f4").tobytes())
    return path


def add_runs(session: Session, count: int, seconds: float = 5.0, gap_after: int = 0, gap: float = 0.0) -> None:
    """Add `count` run files after the existing ones. Add a wall-clock gap after run `gap_after`."""
    timeline = session.timeline()
    start = timeline.total
    wall = T0 + timedelta(seconds=start + sum(g.seconds for g in timeline.gaps()))
    for _ in range(count):
        seq = session.next_seq()
        write_run(session, ramp(start, seconds), wall)
        start += seconds
        wall += timedelta(seconds=seconds + (gap if seq == gap_after else 0.0))


def grow(session: Session, seconds: float) -> None:
    """Append `seconds` of audio to the last run file, as the recorder does."""
    path = session.run_paths()[-1]
    with open(path, "ab") as handle:
        handle.write(ramp(session.timeline().total, seconds).astype("<f4").tobytes())


class FakeBackend:
    """Returns a segment every 2 s of audio, 1.5 s long: words 'at' (0.5 s) and '<time>.' (1 s).

    <time> is the timeline time of the segment start. A segment or word cut by the audio end is
    shorter. A word that would start at the audio end is left out.
    """

    name = "fake"
    model = "m"

    def __init__(self):
        self.calls = 0

    def transcribe(self, samples, prompt):
        self.calls += 1
        length = len(samples) / RATE
        segments = []
        k = 0
        while k * 2 < length - 1e-9:
            start = k * 2
            at = samples[int(start * RATE)] * 1000
            words = [Word(start, min(start + 0.5, length), "at")]
            if start + 0.5 < length - 1e-9:
                words.append(Word(start + 0.5, min(start + 1.5, length), f"{at:.1f}."))
            segments.append(Segment(start, words[-1].end, " ".join(w.text for w in words), words))
            k += 1
        return segments


class GrowingBackend(FakeBackend):
    """FakeBackend that adds `seconds` of audio to the session after each call, as a recorder does."""

    def __init__(self, session, seconds=1.0):
        super().__init__()
        self.session = session
        self.seconds = seconds

    def transcribe(self, samples, prompt):
        segments = super().transcribe(samples, prompt)
        grow(self.session, self.seconds)
        return segments


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.create(tmp_path / "out", "test", now=T0)


def texts(session: Session):
    return [r["text"] for r in Progress(session.partial, {}).records if r["text"]]


# --- recorder run writer ---

def test_writer_appends_blocks_to_one_run_file(session):
    writer = RunWriter(session, rate=1000, channels=2)
    for i in range(9):
        writer.add(np.full((500, 2), i, np.float32), arrived=T0.timestamp() + 0.5)

    [path] = session.run_paths()
    assert path.name == "000001-20261118T140000.000-1000x2.pcm"
    timeline = session.timeline()
    assert timeline.total == 4.5
    assert timeline.gaps() == []
    assert audio.read_frames(timeline.files[0], 4000, 4001).tolist() == [[8.0, 8.0]]
    writer.close()
    assert session.timeline().total == 4.5


def test_second_run_continues_sequence_and_shows_gap(session):
    first = RunWriter(session, 1000, 1)
    first.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 2)
    first.close()
    second = RunWriter(session, 1000, 1)
    second.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 14)

    timeline = session.timeline()
    assert [f.seq for f in timeline.files] == [1, 2]
    [gap] = timeline.gaps()
    assert gap.at == 2.0
    assert gap.seconds == pytest.approx(10.0, abs=0.01)


def frames_of(session):
    [run] = session.timeline().files
    return audio.read_frames(run, 0, run.frames)[:, 0]


def test_fill_gaps_adds_silence_from_stream_start(session):
    writer = RunWriter(session, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    writer.idle(start + 0.3)  # 0.3 s with no block: still within IDLE_SECONDS
    assert session.run_paths() == []
    writer.idle(start + 2.5)
    writer.add(np.ones((500, 1), np.float32), arrived=start + 3.0)

    [path] = session.run_paths()
    assert path.name == "000001-20261118T140000.000-1000x1.pcm"
    data = frames_of(session)
    assert len(data) == 3000
    assert data[:2500].max() == 0 and data[2500:].min() == 1


def test_fill_gaps_adds_silence_between_blocks(session):
    writer = RunWriter(session, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    writer.add(np.ones((1000, 1), np.float32), arrived=start + 1.0)
    writer.idle(start + 1.2)
    writer.add(np.ones((1000, 1), np.float32), arrived=start + 5.0)  # source silent from 1.0 to 4.0

    data = frames_of(session)
    assert len(data) == 5000
    assert data[1000:4000].max() == 0 and data[4000:].min() == 1


def test_fill_gaps_ignores_jitter(session):
    writer = RunWriter(session, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    for i, late in enumerate([0.0, 0.15, 0.0, 0.1]):  # blocks of 0.1 s, some arrive late
        writer.add(np.ones((100, 1), np.float32), arrived=start + 0.1 * (i + 1) + late)
        writer.idle(start + 0.1 * (i + 1) + late)
    assert frames_of(session).min() == 1
    assert writer.frames == 400


def test_writer_without_fill_gaps_writes_only_blocks(session):
    writer = RunWriter(session, 1000, 1)
    writer.begin(T0.timestamp())
    writer.idle(T0.timestamp() + 10)
    writer.add(np.ones((1000, 1), np.float32), arrived=T0.timestamp() + 20)
    assert len(frames_of(session)) == 1000


class FakePyAudio:
    """Stands in for pyaudiowpatch.PyAudio: one default loopback, a stream that sends two blocks."""

    paFloat32, paInputOverflow, paContinue = 1, 2, 0
    LOOPBACKS = [
        {"index": 7, "name": "Speakers (Realtek) [Loopback]", "defaultSampleRate": 48000.0, "maxInputChannels": 2},
        {"index": 9, "name": "Headphones (USB) [Loopback]", "defaultSampleRate": 44100.0, "maxInputChannels": 2},
    ]
    opened = []

    def PyAudio(self):
        return self

    def get_default_wasapi_loopback(self):
        return self.LOOPBACKS[0]

    def get_loopback_device_info_generator(self):
        yield from self.LOOPBACKS

    def open(self, **kwargs):
        self.opened.append(kwargs)
        block = np.array([[0.5, -0.5]] * 4, np.float32).tobytes()
        kwargs["stream_callback"](block, 4, {}, 0)
        kwargs["stream_callback"](block, 4, {}, self.paInputOverflow)
        return types.SimpleNamespace(stop_stream=lambda: None, close=lambda: None)

    def terminate(self):
        pass


@pytest.fixture
def fake_pyaudio(monkeypatch):
    fake = FakePyAudio()
    FakePyAudio.opened = []
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return fake


def test_loopback_stream_reads_default_playback_device(fake_pyaudio):
    blocks = queue.Queue()
    with recorder.loopback_stream(None, blocks) as (name, rate, channels, hint):
        assert (name, rate, channels) == ("Speakers (Realtek) [Loopback]", 48000, 2)
        assert hint == "Check that the app plays to Speakers (Realtek)"
    [opened] = fake_pyaudio.opened
    assert opened["input_device_index"] == 7 and opened["format"] == FakePyAudio.paFloat32
    first = blocks.get_nowait()[0]
    assert first.shape == (4, 2) and first[0].tolist() == [0.5, -0.5]
    assert blocks.get_nowait()[0] == "overflow"


def test_loopback_stream_finds_device_by_name(fake_pyaudio):
    with recorder.loopback_stream("headphones", queue.Queue()) as (name, rate, _, _):
        assert (name, rate) == ("Headphones (USB) [Loopback]", 44100)
    with pytest.raises(SystemExit, match="Loopback devices: Speakers"):
        with recorder.loopback_stream("hdmi", queue.Queue()):
            pass


def test_loopback_needs_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(SystemExit, match="needs Windows"):
        with recorder.loopback_stream(None, queue.Queue()):
            pass


def test_start_passes_source_to_recorder():
    from argparse import Namespace

    from live.cli import recorder_source

    assert recorder_source(Namespace(loopback=False, device=None)) == ["--device", "BlackHole"]
    assert recorder_source(Namespace(loopback=False, device="Cable")) == ["--device", "Cable"]
    assert recorder_source(Namespace(loopback=True, device=None)) == ["--loopback"]
    assert recorder_source(Namespace(loopback=True, device="Head")) == ["--loopback", "--device", "Head"]


def test_other_files_are_not_run_files(session):
    add_runs(session, 1)
    (session.audio_dir / "000002-20261118T140005.000.flac.tmp").write_bytes(b"x")
    (session.audio_dir / "notes.pcm").write_bytes(b"x")
    assert len(session.run_paths()) == 1
    assert session.next_seq() == 2


def test_run_file_counts_only_complete_frames(session):
    path = write_run(session, ramp(0, 1.0), T0)
    with open(path, "ab") as handle:
        handle.write(b"xyz")
    assert session.timeline().total == 1.0


def test_empty_run_file_is_not_on_timeline(session):
    add_runs(session, 1)
    write_run(session, np.zeros(0, np.float32), T0 + timedelta(seconds=60))
    timeline = session.timeline()
    assert len(timeline.files) == 1
    assert timeline.gaps() == []


def test_growing_run_file_extends_timeline(session):
    add_runs(session, 1, seconds=2.0)
    grow(session, 1.5)
    timeline = session.timeline()
    assert timeline.total == 3.5
    samples = audio.read_16k(timeline, 1.0, 3.5)
    assert samples[-1] * 1000 == pytest.approx(3.5, abs=1e-3)


# --- agreement (commit rules) ---


def said(*words):
    """One segment with words (start, end, text). Times are seconds from the span start."""
    items = [Word(*w) for w in words]
    return [Segment(items[0].start, items[-1].end, " ".join(w.text for w in items), items)] if items else []


def step(agreement, words, span_start, span_end, final=False, capped=False):
    return agreement.step(said(*words), span_start, span_end, final=final, capped=capped)


def test_first_pass_writes_nothing_and_shows_all_as_tail():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 1, "One."), (1, 2.5, "two")], 10.0, 13.0)
    assert records == []
    assert caption == console.Caption(10.0, "", "One. two")


def test_agreed_words_up_to_sentence_end_are_written():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "One."), (1, 2.5, "two")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "One."), (1, 2.5, "two"), (2.5, 3.8, "three")], 10.0, 14.0)
    assert records == [{"start": 10.0, "end": 11.0, "text": "One."}]
    assert caption == console.Caption(11.0, "two", "three")


def test_words_agree_by_letters_and_digits_only():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "Hello,"), (1, 2.5, "world")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "hello"), (1, 2.5, "World"), (2.5, 3.8, "Next")], 10.0, 14.0)
    assert records == []
    assert caption == console.Caption(10.0, "hello World", "Next")


def test_agreement_stops_at_first_changed_word():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a"), (1, 2, "b"), (2, 2.9, "c")], 10.0, 13.0)
    _, caption = step(agreement, [(0, 1, "a"), (1, 2, "x"), (2, 3.9, "c")], 10.0, 14.0)
    assert caption == console.Caption(10.0, "a", "x c")


def test_next_pass_drops_agreed_words_it_gives_again():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a"), (1, 2.5, "b")], 10.0, 13.0)
    step(agreement, [(0, 1, "a"), (1, 2.5, "b"), (2.5, 3.8, "c")], 10.0, 14.0)
    # agreed: a b (b ends at 12.5). Here b comes again, later than the time filter allows.
    _, caption = step(agreement, [(0, 1, "a"), (2.45, 2.6, "b"), (2.6, 3.8, "c"), (3.8, 4.9, "d")], 10.0, 15.0)
    assert caption == console.Caption(10.0, "a b c", "d")


def test_pause_writes_all_words_as_sentence_lines():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 1, "One."), (1, 2, "two")], 10.0, 14.0)
    assert records == [
        {"start": 10.0, "end": 11.0, "text": "One."},
        {"start": 11.0, "end": 12.0, "text": "two"},
    ]
    assert caption is None


def test_long_agreed_words_are_written_up_to_clause_end():
    agreement = transcriber.Agreement()
    words = [(0, 4, "a"), (4, 6, "b,"), (6, 8.5, "c"), (8.5, 9.8, "d")]
    step(agreement, words, 0.0, 10.0)
    records, caption = step(agreement, words + [(9.8, 10.9, "e")], 0.0, 11.0)
    assert [r["text"] for r in records] == ["a b,"]
    assert caption == console.Caption(6.0, "c d", "e")


def test_long_agreed_words_with_no_clause_end_are_all_written():
    agreement = transcriber.Agreement()
    words = [(0, 4, "a"), (4, 8.5, "b"), (8.5, 9.8, "c")]
    step(agreement, words, 0.0, 10.0)
    records, caption = step(agreement, words + [(9.8, 10.9, "d")], 0.0, 11.0)
    assert [r["text"] for r in records] == ["a b c"]
    assert caption == console.Caption(9.8, "", "d")


def test_final_writes_all_marks_span_done_and_resets():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a"), (1, 2.5, "b")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "a"), (1, 2.9, "b")], 10.0, 13.0, final=True)
    assert records == [{"start": 10.0, "end": 12.9, "text": "a b"}, {"start": 12.9, "end": 13.0, "text": ""}]
    assert caption is None
    assert agreement.agreed == agreement.tail == []


def test_capped_writes_all_but_last_word():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 10, "a"), (10, 29.5, "b")], 0.0, 30.0, capped=True)
    assert [r["text"] for r in records] == ["a"]
    assert caption == console.Caption(10.0, "", "b")


def test_silence_advances_but_keeps_tail():
    records, caption = transcriber.Agreement().step([], 10.0, 20.0, final=False, capped=False)
    assert records == [{"start": 10.0, "end": 18.0, "text": ""}]
    assert caption is None


def test_short_silence_does_not_go_back():
    assert transcriber.Agreement().step([], 10.0, 11.0, final=False, capped=False) == ([], None)


def test_empty_word_text_is_left_out():
    records, caption = step(transcriber.Agreement(), [(0, 1, ""), (1, 2.5, "b")], 10.0, 13.0)
    assert caption == console.Caption(11.0, "", "b")


# --- terminal output ---


def test_fit_keeps_short_text():
    assert console.fit("[00:00:01] ", "hello", 80) == "[00:00:01] hello"


def test_fit_keeps_end_of_long_text():
    line = console.fit("[00:00:01] ", "one two three four", 20)
    assert line == "[00:00:01] …ee four"
    assert len(line) == 19


def test_styled_shows_agreed_plain_and_tail_dim():
    text = console.styled(console.Caption(1.0, "one two", "three"), 80)
    assert text == f"[00:00:01] one two {console.DIM}three{console.RESET}"


def test_styled_cut_inside_tail_is_all_dim():
    text = console.styled(console.Caption(1.0, "one", "two three four"), 20)
    assert text == f"[00:00:01] {console.DIM}…ee four{console.RESET}"


class Tty:
    """stdout that says it is a terminal and keeps what is written."""

    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text

    def flush(self):
        pass

    def isatty(self):
        return True


def wide_terminal(monkeypatch):
    out = Tty()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr(console.shutil, "get_terminal_size", lambda: os.terminal_size((200, 24)))
    return out


def test_live_pass_shows_draft_then_fixed_line_replaces_it(session, monkeypatch):
    out = wide_terminal(monkeypatch)
    add_runs(session, 1, seconds=10.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    tail = " ".join(f"at {s}.0." for s in range(0, 9, 2))
    assert out.text == f"{console.ERASE}[00:00:00] {console.DIM}{tail}{console.RESET}{console.ERASE}"

    out.text = ""
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)
    assert out.text.startswith(f"{console.ERASE}[00:00:00] at 0.0.\n")
    assert console.DIM not in out.text


def test_no_draft_when_stdout_is_not_a_terminal(session, capsys):
    add_runs(session, 1, seconds=10.0)
    backend = GrowingBackend(session)
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert capsys.readouterr().out == "".join(f"[00:00:0{s}] at {s}.0.\n" for s in (0, 2, 4, 6, 8))


# --- transcriber loop ---

ALL_15 = [f"at {s}.0." for s in range(0, 15, 2)]


def test_stopped_session_is_transcribed_to_the_end(session):
    add_runs(session, 3)
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)

    assert texts(session) == ALL_15
    assert Progress(session.partial, {}).end == pytest.approx(15.0)
    body = session.transcript.read_text().splitlines()
    assert body[0] == f"{session.name}-live"
    assert body[2:] == [f"[00:00:{s:02d}] at {s}.0." for s in range(0, 15, 2)]


def test_live_passes_write_agreed_sentences_then_continue(session):
    add_runs(session, 1, seconds=10.0)
    backend = GrowingBackend(session)
    assert not transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert texts(session) == [f"at {s}.0." for s in range(0, 9, 2)]
    assert Progress(session.partial, {}).end == pytest.approx(9.5)

    grow(session, 4.0)
    session.mark_stopped()
    assert transcriber.run(session, FakeBackend(), poll=0)
    # 2 passes grew the audio to 12 s, then 4 s more: the last segment starts 0.5 s before the end.
    assert texts(session)[5:] == ["at 9.5.", "at 11.5.", "at 13.5.", "at"]


def test_live_pass_waits_for_step_of_new_audio(session):
    add_runs(session, 1, seconds=4.0)
    backend = FakeBackend()
    polls = []

    def stop_after_polls():
        polls.append(1)
        return len(polls) > 5

    transcriber.run(session, backend, should_exit=stop_after_polls, poll=0)
    assert backend.calls == 1


def test_resume_after_crash_removes_cut_line_and_continues(session):
    add_runs(session, 1, seconds=10.0)
    backend = GrowingBackend(session, seconds=5.0)
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert texts(session) == [f"at {s}.0." for s in range(0, 9, 2)]
    with session.partial.open("a") as handle:
        handle.write('{"start": 99, "en')
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(session)[5:] == ["at 9.5.", "at 11.5.", "at 13.5.", "at 15.5.", "at 17.5.", "at"]


def test_pass_stops_at_gap_and_commits_all_before_it(session):
    add_runs(session, 2, gap_after=1, gap=30.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)

    records = Progress(session.partial, {}).records
    assert records[-1]["end"] == 5.0
    assert [r["text"] for r in records if r["text"]] == ["at 0.0.", "at 2.0.", "at 4.0."]


def test_prompt_does_not_cross_gap(session):
    add_runs(session, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    prompts = []

    class Recording(FakeBackend):
        def transcribe(self, samples, prompt):
            prompts.append(prompt)
            return super().transcribe(samples, prompt)

    transcriber.run(session, Recording(), poll=0)
    assert prompts == [None, None]


def test_gap_line_in_transcript(session):
    add_runs(session, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)

    body = session.transcript.read_text()
    assert "[00:00:05] (recording gap 00:00:30)\n" in body


# --- merge and finalize ---

def test_merge_joins_run_files_exactly(session, tmp_path):
    add_runs(session, 3, seconds=2.5)
    files = session.timeline().files
    target = tmp_path / "merged.flac"

    frames = audio.merge(files, target)

    data, rate = soundfile.read(str(target), dtype="float32")
    assert rate == RATE and frames == len(data) == 3 * int(2.5 * RATE)
    assert np.allclose(data, ramp(0, 7.5), atol=2 ** -22)


def test_merge_converts_other_rate_and_channels(session, tmp_path):
    add_runs(session, 1, seconds=1.0)
    write_run(session, np.zeros((48000, 2), np.float32), T0 + timedelta(seconds=1), rate=48000)

    frames = audio.merge(session.timeline().files, tmp_path / "m.flac")

    info = soundfile.info(str(tmp_path / "m.flac"))
    assert (info.samplerate, info.channels, info.frames) == (RATE, 1, frames)
    assert frames == pytest.approx(2 * RATE, abs=2)


def test_merge_resamples_in_blocks_like_one_piece(session, tmp_path, monkeypatch):
    add_runs(session, 1, seconds=1.0)
    tone = np.sin(np.arange(3 * 48000) * 2 * np.pi * 440 / 48000).astype(np.float32) * 0.5
    write_run(session, tone, T0 + timedelta(seconds=1), rate=48000)
    monkeypatch.setattr(audio, "BLOCK_FRAMES", 10000)

    frames = audio.merge(session.timeline().files, tmp_path / "m.flac")

    data, _ = soundfile.read(str(tmp_path / "m.flac"), dtype="float32")
    whole = audio.soxr.resample(tone, 48000, RATE)
    assert frames == pytest.approx(4 * RATE, abs=2)
    n = min(len(whole), frames - RATE)
    assert np.abs(data[RATE:RATE + n] - whole[:n]).max() < 1e-3


def test_read_16k_spans_run_files(session):
    add_runs(session, 2)
    samples = audio.read_16k(session.timeline(), 4.0, 6.0)
    assert len(samples) == 2 * RATE
    assert samples[0] * 1000 == pytest.approx(4.0, abs=1e-3)
    assert samples[-1] * 1000 == pytest.approx(6.0, abs=1e-3)


def test_finalize_writes_txt_and_flac_and_removes_work_files(session, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    add_runs(session, 2)

    finalize_module.finalize(session)

    assert session.transcript.read_text().splitlines()[2] == "[00:00:00] at 0.0."
    assert soundfile.info(str(session.merged)).frames == 10 * RATE
    assert not session.audio_dir.exists()
    assert not session.partial.exists()
    assert not session.is_stopped()


def test_finalize_keeps_run_files_when_merge_check_fails(session, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    monkeypatch.setattr(audio, "count_frames", lambda path: 0)
    add_runs(session, 2)

    with pytest.raises(audio.MergeFailed):
        finalize_module.finalize(session)

    assert len(session.run_paths()) == 2
    assert session.partial.exists()
    assert not session.merged.exists()


def test_finalize_after_merge_only_cleans_up(session, monkeypatch):
    def no_backend(name, model):
        raise AssertionError("must not transcribe again")

    add_runs(session, 1)
    session.merged.write_bytes(b"checked before")
    session.partial.write_text('{"header": {}}\n')
    monkeypatch.setattr(backends, "make", no_backend)

    finalize_module.finalize(session)

    assert session.merged.read_bytes() == b"checked before"
    assert not session.audio_dir.exists()
