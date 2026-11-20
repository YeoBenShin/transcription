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
from live import audio, backends, console, finalize as finalize_module, loops, transcriber, vad
from live.backends import Segment, Word
from live.progress import Progress
from live import recorder
from live.recorder import RunWriter
from live.session import MIC, SYSTEM, Session, Stream, run_name

RATE = 16000
T0 = datetime(2026, 11, 18, 14, 0, 0)


def ramp(start: float, seconds: float) -> np.ndarray:
    """Mono samples whose value is (timeline time / 1000)."""
    return ((start + np.arange(int(seconds * RATE)) / RATE) / 1000).astype(np.float32)


def write_run(system: Session, data: np.ndarray, wall: datetime, rate: int = RATE) -> Path:
    """Write `data` (frames, or frames x channels) as the next run file."""
    data = data if data.ndim == 2 else data[:, None]
    path = system.audio_dir / run_name(system.next_seq(), wall, rate, data.shape[1])
    path.write_bytes(data.astype("<f4").tobytes())
    return path


def add_runs(system: Session, count: int, seconds: float = 5.0, gap_after: int = 0, gap: float = 0.0) -> None:
    """Add `count` run files after the existing ones. Add a wall-clock gap after run `gap_after`."""
    timeline = system.timeline()
    start = timeline.total
    wall = T0 + timedelta(seconds=start + sum(g.seconds for g in timeline.gaps()))
    for _ in range(count):
        seq = system.next_seq()
        write_run(system, ramp(start, seconds), wall)
        start += seconds
        wall += timedelta(seconds=seconds + (gap if seq == gap_after else 0.0))


def grow(system: Session, seconds: float) -> None:
    """Append `seconds` of audio to the last run file, as the recorder does."""
    path = system.run_paths()[-1]
    with open(path, "ab") as handle:
        handle.write(ramp(system.timeline().total, seconds).astype("<f4").tobytes())


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

    def __init__(self, system, seconds=1.0):
        super().__init__()
        self.system = system
        self.seconds = seconds

    def transcribe(self, samples, prompt):
        segments = super().transcribe(samples, prompt)
        grow(self.system, self.seconds)
        return segments


def fake_speech_end(samples):
    """VAD speech end for FakeBackend audio: speech is [2k, 2k + 1.5) s. The speech ends at the
    last word end if vad.PAUSE_SECONDS or more of audio follow it, else at the audio end.
    """
    length = len(samples) / RATE
    if length <= 0:
        return None
    end = min((np.ceil(length / 2) - 1) * 2 + 1.5, length)
    return end if length - end >= vad.PAUSE_SECONDS else length


@pytest.fixture(autouse=True)
def no_silero(monkeypatch):
    monkeypatch.setattr(vad, "speech_end", fake_speech_end)


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session.create(tmp_path / "out", "test", now=T0)


@pytest.fixture
def system(session: Session) -> Stream:
    stream = session.stream(SYSTEM)
    stream.audio_dir.mkdir()
    return stream


def texts(system: Session):
    return [r["text"] for r in Progress(system.partial, {}).records if r["text"]]


# --- recorder run writer ---

def test_writer_appends_blocks_to_one_run_file(session, system):
    writer = RunWriter(system, rate=1000, channels=2)
    for i in range(9):
        writer.add(np.full((500, 2), i, np.float32), arrived=T0.timestamp() + 0.5)

    [path] = system.run_paths()
    assert path.name == "000001-20261118T140000.000-1000x2.pcm"
    timeline = system.timeline()
    assert timeline.total == 4.5
    assert timeline.gaps() == []
    assert audio.read_frames(timeline.files[0], 4000, 4001).tolist() == [[8.0, 8.0]]
    writer.close()
    assert system.timeline().total == 4.5


def test_second_run_continues_sequence_and_shows_gap(session, system):
    first = RunWriter(system, 1000, 1)
    first.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 2)
    first.close()
    second = RunWriter(system, 1000, 1)
    second.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 14)

    timeline = system.timeline()
    assert [f.seq for f in timeline.files] == [1, 2]
    [gap] = timeline.gaps()
    assert gap.at == 2.0
    assert gap.seconds == pytest.approx(10.0, abs=0.01)


def frames_of(system):
    [run] = system.timeline().files
    return audio.read_frames(run, 0, run.frames)[:, 0]


def test_fill_gaps_adds_silence_from_stream_start(session, system):
    writer = RunWriter(system, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    writer.idle(start + 0.3)  # 0.3 s with no block: still within IDLE_SECONDS
    assert system.run_paths() == []
    writer.idle(start + 2.5)
    writer.add(np.ones((500, 1), np.float32), arrived=start + 3.0)

    [path] = system.run_paths()
    assert path.name == "000001-20261118T140000.000-1000x1.pcm"
    data = frames_of(system)
    assert len(data) == 3000
    assert data[:2500].max() == 0 and data[2500:].min() == 1


def test_fill_gaps_adds_silence_between_blocks(session, system):
    writer = RunWriter(system, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    writer.add(np.ones((1000, 1), np.float32), arrived=start + 1.0)
    writer.idle(start + 1.2)
    writer.add(np.ones((1000, 1), np.float32), arrived=start + 5.0)  # source silent from 1.0 to 4.0

    data = frames_of(system)
    assert len(data) == 5000
    assert data[1000:4000].max() == 0 and data[4000:].min() == 1


def test_fill_gaps_ignores_jitter(session, system):
    writer = RunWriter(system, 1000, 1, fill_gaps=True)
    start = T0.timestamp()
    writer.begin(start)
    for i, late in enumerate([0.0, 0.15, 0.0, 0.1]):  # blocks of 0.1 s, some arrive late
        writer.add(np.ones((100, 1), np.float32), arrived=start + 0.1 * (i + 1) + late)
        writer.idle(start + 0.1 * (i + 1) + late)
    assert frames_of(system).min() == 1
    assert writer.frames == 400


def test_writer_without_fill_gaps_writes_only_blocks(session, system):
    writer = RunWriter(system, 1000, 1)
    writer.begin(T0.timestamp())
    writer.idle(T0.timestamp() + 10)
    writer.add(np.ones((1000, 1), np.float32), arrived=T0.timestamp() + 20)
    assert len(frames_of(system)) == 1000


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


def test_start_passes_sources_to_recorders():
    from argparse import Namespace

    from live.cli import recorder_sources

    def sources(loopback=False, device=None, mic=None, mic_only=False):
        return recorder_sources(Namespace(loopback=loopback, device=device, mic=mic, mic_only=mic_only))

    assert sources() == {"system": ["--device", "BlackHole"]}
    assert sources(device="Cable") == {"system": ["--device", "Cable"]}
    assert sources(loopback=True) == {"system": ["--loopback"]}
    assert sources(loopback=True, device="Head") == {"system": ["--loopback", "--device", "Head"]}
    assert sources(mic="") == {"system": ["--device", "BlackHole"], "mic": []}
    assert sources(loopback=True, mic="USB") == {"system": ["--loopback"], "mic": ["--device", "USB"]}
    assert sources(mic_only=True) == {"mic": []}
    assert sources(mic_only=True, mic="USB") == {"mic": ["--device", "USB"]}


class FakeChildProcess:
    """A child process: adds `work` to the shared progress count at start, exits when the test sets `code`."""

    def __init__(self, counter: dict, work: int):
        self.code = None
        self.stdin = types.SimpleNamespace(closed=False)
        self.stdin.close = lambda: setattr(self.stdin, "closed", True)
        counter["progress"] += work

    def poll(self):
        return self.code


@pytest.fixture
def fake_children(monkeypatch):
    """Patch live.cli.spawn. Each start takes the next amount of work from the returned list.
    Returns (plan, started, make), where make() is a Child with a progress count that the starts grow."""
    from live import cli

    plan, started, counter = [], [], {"progress": 0}

    def spawn(module, args):
        started.append(FakeChildProcess(counter, plan.pop(0)))
        return started[-1]

    monkeypatch.setattr(cli, "spawn", spawn)
    return plan, started, lambda: cli.Child("test child", "live.test", [], lambda: counter["progress"])


def test_child_that_did_work_restarts_after_delay(fake_children):
    from live.cli import CHILD_RESTART_DELAY

    plan, started, make = fake_children
    plan += [1, 1]
    child = make()
    started[0].code = -9
    assert child.check(100.0)
    assert len(started) == 1
    assert child.check(100.0 + CHILD_RESTART_DELAY - 0.1)
    assert len(started) == 1
    assert child.check(100.0 + CHILD_RESTART_DELAY)
    assert len(started) == 2 and child.process is started[1]


def test_child_exit_code_0_also_restarts(fake_children):
    plan, started, make = fake_children
    plan += [1]
    child = make()
    started[0].code = 0
    assert child.check(0.0) and child.restart_at is not None


def test_child_that_fails_at_first_start_is_not_restarted(fake_children):
    plan, started, make = fake_children
    plan += [0]
    child = make()
    started[0].code = 1
    assert not child.check(100.0)


def test_child_stops_after_restarts_in_a_row_with_no_progress(fake_children):
    from live.cli import CHILD_RESTARTS

    plan, started, make = fake_children
    plan += [1] + [0] * CHILD_RESTARTS
    child = make()
    now = 0.0
    for _ in range(CHILD_RESTARTS):
        child.process.code = 1
        assert child.check(now)
        now += 10.0
        assert child.check(now)
    child.process.code = 1
    assert not child.check(now)
    assert len(started) == 1 + CHILD_RESTARTS


def test_child_restart_with_progress_resets_the_count(fake_children):
    from live.cli import CHILD_RESTARTS

    plan, started, make = fake_children
    plan += [1, 0, 1] + [0] * CHILD_RESTARTS
    child = make()
    now = 0.0
    for _ in range(2 + CHILD_RESTARTS):
        child.process.code = 1
        assert child.check(now)
        now += 10.0
        child.check(now)
    child.process.code = 1
    assert not child.check(now)


def test_child_stop_closes_stdin_and_cancels_restart(fake_children):
    from live.cli import CHILD_RESTART_DELAY

    plan, started, make = fake_children
    plan += [1, 1]
    child = make()
    child.stop()
    assert started[0].stdin.closed
    started[0].code = 0
    assert child.check(0.0) and child.restart_at == CHILD_RESTART_DELAY
    child.stop()
    assert child.restart_at is None
    assert len(started) == 1


def test_child_start_planned_starts_without_wait(fake_children):
    plan, started, make = fake_children
    plan += [1, 1]
    child = make()
    child.start_planned()
    assert len(started) == 1
    started[0].code = 1
    child.check(0.0)
    child.start_planned()
    assert len(started) == 2 and child.restart_at is None


def test_recorder_progress_is_run_files_and_transcriber_progress_is_progress_bytes(session, system):
    from live.cli import transcript_progress

    assert transcript_progress(session) == 0
    progress = Progress(system.partial, {"model": "tiny"})
    assert transcript_progress(session) == 0
    record = '{"start": 0, "end": 1, "text": "a"}\n'
    progress.handle.write(record)
    progress.handle.flush()
    assert transcript_progress(session) == len(record.replace("\n", os.linesep))  # text mode: CRLF on Windows
    assert len(system.run_paths()) == 0
    RunWriter(system, rate=1000, channels=1).add(np.zeros((10, 1), np.float32), arrived=T0.timestamp())
    assert len(system.run_paths()) == 1


def test_mic_only_refuses_system_options():
    from live.cli import main

    for extra in (["--device", "BlackHole"], ["--loopback"]):
        with pytest.raises(SystemExit):
            main(["start", "--mic-only", *extra])


def test_other_files_are_not_run_files(session, system):
    add_runs(system, 1)
    (system.audio_dir / "000002-20261118T140005.000.flac.tmp").write_bytes(b"x")
    (system.audio_dir / "notes.pcm").write_bytes(b"x")
    assert len(system.run_paths()) == 1
    assert system.next_seq() == 2


def test_run_file_counts_only_complete_frames(session, system):
    path = write_run(system, ramp(0, 1.0), T0)
    with open(path, "ab") as handle:
        handle.write(b"xyz")
    assert system.timeline().total == 1.0


def test_empty_run_file_is_not_on_timeline(session, system):
    add_runs(system, 1)
    write_run(system, np.zeros(0, np.float32), T0 + timedelta(seconds=60))
    timeline = system.timeline()
    assert len(timeline.files) == 1
    assert timeline.gaps() == []


def test_growing_run_file_extends_timeline(session, system):
    add_runs(system, 1, seconds=2.0)
    grow(system, 1.5)
    timeline = system.timeline()
    assert timeline.total == 3.5
    samples = audio.read_16k(timeline, 1.0, 3.5)
    assert samples[-1] * 1000 == pytest.approx(3.5, abs=1e-3)


# --- agreement (commit rules) ---


def said(*words):
    """One segment with words (start, end, text). Times are seconds from the span start."""
    items = [Word(*w) for w in words]
    return [Segment(items[0].start, items[-1].end, " ".join(w.text for w in items), items)] if items else []


def step(agreement, words, span_start, span_end, final=False, capped=False, speech_end="span_end"):
    """One Agreement step. speech_end "span_end" means the speech goes on to the span end."""
    end = span_end if speech_end == "span_end" else speech_end
    return agreement.step(said(*words), span_start, span_end, final=final, capped=capped, speech_end=end)


def test_first_pass_writes_nothing_and_shows_all_as_tail():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 1, "One."), (1, 2.5, "two")], 10.0, 13.0)
    assert records == []
    assert caption == console.Caption(10.0, "", "One. two")


def test_agreed_words_are_written_at_once_into_the_open_line():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "One"), (1, 2.5, "two")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "One"), (1, 2.5, "two"), (2.5, 3.8, "three")], 10.0, 14.0)
    assert records == [{"start": 10.0, "end": 12.5, "text": "One two", "open": True}]
    assert caption == console.Caption(10.0, "One two", "three")


def test_line_closes_at_sentence_end_with_an_agreed_word_after_it():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "One."), (1, 2.5, "two")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "One."), (1, 2.5, "two"), (2.5, 3.8, "three")], 10.0, 14.0)
    assert records == [
        {"start": 10.0, "end": 11.0, "text": "One."},
        {"start": 11.0, "end": 12.5, "text": "two", "open": True},
    ]
    assert caption == console.Caption(11.0, "two", "three")


def test_sentence_end_at_the_end_of_agreed_words_waits():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a"), (1, 2.5, "b.")], 10.0, 13.0)
    records, _ = step(agreement, [(0, 1, "a"), (1, 2.5, "b."), (2.6, 3.8, "c")], 10.0, 13.9)
    assert records == [{"start": 10.0, "end": 12.5, "text": "a b.", "open": True}]
    # the next word agrees: a record with text "" closes the line before it
    records, caption = step(agreement, [(0, 1, "a"), (1, 2.5, "b."), (2.6, 3.8, "c"), (3.8, 4.5, "d")], 10.0, 14.6)
    assert records == [
        {"start": 12.5, "end": 12.5, "text": ""},
        {"start": 12.6, "end": 13.8, "text": "c", "open": True},
    ]
    assert caption == console.Caption(12.6, "c", "d")


def test_sentence_end_closes_after_guard_of_audio():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a"), (1, 2.5, "b.")], 10.0, 13.0)
    step(agreement, [(0, 1, "a"), (1, 2.5, "b.")], 10.0, 13.9)
    records, caption = step(agreement, [(0, 1, "a"), (1, 2.5, "b.")], 10.0, 14.0)
    assert records == [{"start": 12.5, "end": 12.5, "text": ""}]
    assert caption is None


def test_words_agree_by_letters_and_digits_only():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "Hello,"), (1, 2.5, "world")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "hello"), (1, 2.5, "World"), (2.5, 3.8, "Next")], 10.0, 14.0)
    assert [r["text"] for r in records] == ["hello World"]
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
    records, caption = step(
        agreement, [(0, 1, "a"), (2.45, 2.6, "b"), (2.6, 3.8, "c"), (3.8, 4.9, "d")], 10.0, 15.0
    )
    assert records == [{"start": 12.6, "end": 13.8, "text": "c", "open": True}]
    assert caption == console.Caption(10.0, "a b c", "d")


def test_vad_speech_end_writes_all_words_and_closes_the_line():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 1, "One."), (1, 2, "two")], 10.0, 14.0, speech_end=12.1)
    assert records == [
        {"start": 10.0, "end": 11.0, "text": "One."},
        {"start": 11.0, "end": 12.0, "text": "two"},
    ]
    assert caption is None


def test_vad_speech_end_closes_an_open_line_with_no_new_word():
    agreement = transcriber.Agreement()
    step(agreement, [(0, 1, "a")], 10.0, 12.0)
    step(agreement, [(0, 1, "a"), (1.5, 2.5, "b")], 10.0, 13.0)
    records, caption = step(agreement, [(0, 1, "a")], 10.0, 14.0, speech_end=11.0)
    assert records == [{"start": 11.0, "end": 11.0, "text": ""}]
    assert caption is None


def test_last_word_end_alone_is_not_a_pause():
    agreement = transcriber.Agreement()
    records, caption = step(agreement, [(0, 1, "One."), (1, 2, "two")], 10.0, 16.0)
    assert records == []
    assert caption == console.Caption(10.0, "", "One. two")


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
    assert records == [{"start": 0.0, "end": 10.0, "text": "a"}]
    assert caption == console.Caption(10.0, "", "b")


def test_silence_advances_but_keeps_tail():
    records, caption = step(transcriber.Agreement(), [], 10.0, 20.0, speech_end=None)
    assert records == [{"start": 10.0, "end": 18.0, "text": ""}]
    assert caption is None


def test_short_silence_does_not_go_back():
    assert step(transcriber.Agreement(), [], 10.0, 11.0, speech_end=None) == ([], None)


def test_empty_word_text_is_left_out():
    records, caption = step(transcriber.Agreement(), [(0, 1, ""), (1, 2.5, "b")], 10.0, 13.0)
    assert caption == console.Caption(11.0, "", "b")


def test_segment_is_cut_where_a_loop_repeats():
    words = [(0, 1, "So"), (1, 2, "I"), (2, 3, "mean,"), (3, 4, "I"), (4, 5, "mean,"), (5, 6, "I"), (6, 7, "mean")]
    placed = transcriber.place(said(*words), 10.0, 20.0)
    assert [w.text for w in placed] == ["So", "I", "mean,"]


def test_open_records_give_the_agreed_words_on_resume():
    words = transcriber.open_words([{"start": 1.0, "end": 2.0, "text": "a b", "open": True}])
    assert words == [Word(1.0, 1.5, "a"), Word(1.5, 2.0, "b")]


# --- loops ---


def test_loop_cut_keeps_the_first_copy():
    assert loops.loop_cut("a b c b c b c d".split()) == 3
    assert loops.loop_cut("tell tell tell tell".split()) == 1


def test_two_copies_are_not_a_loop():
    assert loops.loop_cut("and and I I think".split()) is None
    assert loops.loop_cut([]) is None


def test_has_loop_by_ngram_or_compression():
    assert loops.has_loop("no no no")
    assert loops.has_loop("ab" * 40)
    assert not loops.has_loop("A normal line of speech, with no loop in it.")


# --- progress lines and prompt ---


def write_records(session, records):
    progress = Progress(session.stream(SYSTEM).partial, {})
    progress.append(records)
    return progress


def test_open_records_join_the_next_record_into_one_line(session):
    progress = write_records(session, [
        {"start": 0.0, "end": 1.0, "text": "a b", "open": True},
        {"start": 1.0, "end": 2.0, "text": "c."},
        {"start": 2.0, "end": 3.0, "text": "d", "open": True},
    ])
    assert progress.lines() == [Line(0.0, "a b c."), Line(2.0, "d")]
    assert [line.closed for line in progress.text_lines()] == [True, False]
    assert progress.end == 2.0
    assert progress.open_records() == [{"start": 2.0, "end": 3.0, "text": "d", "open": True}]


def test_text_record_closes_an_open_line(session):
    progress = write_records(session, [
        {"start": 0.0, "end": 1.0, "text": "a", "open": True},
        {"start": 1.0, "end": 1.0, "text": ""},
    ])
    assert progress.lines() == [Line(0.0, "a")]
    assert progress.end == 1.0


def test_prompt_is_the_last_chars_of_lines_before_the_span(session):
    progress = write_records(session, [
        {"start": 0.0, "end": 1.0, "text": "early words here."},
        {"start": 1.0, "end": 2.0, "text": "no no no no."},
        {"start": 2.0, "end": 3.0, "text": "later words."},
        {"start": 3.0, "end": 4.0, "text": "inside span", "open": True},
    ])
    assert progress.prompt(since=0.0, before=3.0) == "early words here. later words."
    assert progress.prompt(since=0.0, before=3.0, chars=17) == "here. later words."[-17:].split(" ", 1)[1]
    assert progress.prompt(since=0.0, before=3.0, chars=18) == "here. later words."
    assert progress.prompt(since=1.5, before=3.0) == "later words."
    assert progress.prompt(since=3.0, before=3.0) is None


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


def test_live_pass_shows_draft_then_fixed_line_replaces_it(session, monkeypatch, system):
    out = wide_terminal(monkeypatch)
    add_runs(system, 1, seconds=9.8)  # 0.3 s after the last word: no pause
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    tail = " ".join(f"at {s}.0." for s in range(0, 9, 2))
    assert out.text == f"{console.ERASE}[00:00:00] {console.DIM}{tail}{console.RESET}{console.ERASE}"

    out.text = ""
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)
    assert out.text.startswith(f"{console.ERASE}[00:00:00] at 0.0.\n")
    assert console.DIM not in out.text


def test_no_draft_when_stdout_is_not_a_terminal(session, capsys, system):
    add_runs(system, 1, seconds=10.0)
    backend = GrowingBackend(system)
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert capsys.readouterr().out == "".join(f"[00:00:0{s}] at {s}.0.\n" for s in (0, 2, 4, 6, 8))


# --- transcriber loop ---

ALL_15 = [f"at {s}.0." for s in range(0, 15, 2)]


def test_stopped_session_is_transcribed_to_the_end(session, system):
    add_runs(system, 3)
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)

    assert texts(system) == ALL_15
    assert Progress(system.partial, {}).end == pytest.approx(15.0)
    body = session.transcript.read_text().splitlines()
    assert body[0] == f"{session.name}-live"
    assert body[2:] == [f"[00:00:{s:02d}] at {s}.0." for s in range(0, 15, 2)]


def test_live_passes_write_agreed_sentences_then_continue(session, system):
    add_runs(system, 1, seconds=10.0)
    backend = GrowingBackend(system)
    assert not transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert texts(system) == [f"at {s}.0." for s in range(0, 9, 2)]
    assert Progress(system.partial, {}).end == pytest.approx(9.5)

    grow(system, 4.0)
    session.mark_stopped()
    assert transcriber.run(session, FakeBackend(), poll=0)
    # 2 passes grew the audio to 12 s, then 4 s more: the last segment starts 0.5 s before the end.
    assert texts(system)[5:] == ["at 9.5.", "at 11.5.", "at 13.5.", "at"]


def test_live_pass_waits_for_step_of_new_audio(session, system):
    add_runs(system, 1, seconds=4.0)
    backend = FakeBackend()
    polls = []

    def stop_after_polls():
        polls.append(1)
        return len(polls) > 5

    transcriber.run(session, backend, should_exit=stop_after_polls, poll=0)
    assert backend.calls == 1


def test_resume_after_crash_removes_cut_line_and_continues(session, system):
    add_runs(system, 1, seconds=10.0)
    backend = GrowingBackend(system, seconds=5.0)
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 2, poll=0)
    assert texts(system) == [f"at {s}.0." for s in range(0, 9, 2)]
    with system.partial.open("a") as handle:
        handle.write('{"start": 99, "en')
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(system)[5:] == ["at 9.5.", "at 11.5.", "at 13.5.", "at 15.5.", "at 17.5.", "at"]


def test_pass_stops_at_gap_and_commits_all_before_it(session, system):
    add_runs(system, 2, gap_after=1, gap=30.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)

    records = Progress(system.partial, {}).records
    assert records[-1]["end"] == 5.0
    assert [r["text"] for r in records if r["text"]] == ["at 0.0.", "at 2.0.", "at 4.0."]


def test_prompt_does_not_cross_gap(session, system):
    add_runs(system, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    prompts = []

    class Recording(FakeBackend):
        def transcribe(self, samples, prompt):
            prompts.append(prompt)
            return super().transcribe(samples, prompt)

    transcriber.run(session, Recording(), poll=0)
    assert prompts == [None, None]


def test_gap_line_in_transcript(session, system):
    add_runs(system, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)

    body = session.transcript.read_text()
    assert "[00:00:05] (recording gap 00:00:30)\n" in body


# --- merge and finalize ---

def test_merge_joins_run_files_exactly(session, tmp_path, system):
    add_runs(system, 3, seconds=2.5)
    files = system.timeline().files
    target = tmp_path / "merged.flac"

    frames = audio.merge(files, target)

    data, rate = soundfile.read(str(target), dtype="float32")
    assert rate == RATE and frames == len(data) == 3 * int(2.5 * RATE)
    assert np.allclose(data, ramp(0, 7.5), atol=2 ** -22)


def test_merge_converts_other_rate_and_channels(session, tmp_path, system):
    add_runs(system, 1, seconds=1.0)
    write_run(system, np.zeros((48000, 2), np.float32), T0 + timedelta(seconds=1), rate=48000)

    frames = audio.merge(system.timeline().files, tmp_path / "m.flac")

    info = soundfile.info(str(tmp_path / "m.flac"))
    assert (info.samplerate, info.channels, info.frames) == (RATE, 1, frames)
    assert frames == pytest.approx(2 * RATE, abs=2)


def test_merge_resamples_in_blocks_like_one_piece(session, tmp_path, monkeypatch, system):
    add_runs(system, 1, seconds=1.0)
    tone = np.sin(np.arange(3 * 48000) * 2 * np.pi * 440 / 48000).astype(np.float32) * 0.5
    write_run(system, tone, T0 + timedelta(seconds=1), rate=48000)
    monkeypatch.setattr(audio, "BLOCK_FRAMES", 10000)

    frames = audio.merge(system.timeline().files, tmp_path / "m.flac")

    data, _ = soundfile.read(str(tmp_path / "m.flac"), dtype="float32")
    whole = audio.soxr.resample(tone, 48000, RATE)
    assert frames == pytest.approx(4 * RATE, abs=2)
    n = min(len(whole), frames - RATE)
    assert np.abs(data[RATE:RATE + n] - whole[:n]).max() < 1e-3


def test_read_16k_spans_run_files(session, system):
    add_runs(system, 2)
    samples = audio.read_16k(system.timeline(), 4.0, 6.0)
    assert len(samples) == 2 * RATE
    assert samples[0] * 1000 == pytest.approx(4.0, abs=1e-3)
    assert samples[-1] * 1000 == pytest.approx(6.0, abs=1e-3)


def test_finalize_writes_txt_and_flac_and_removes_work_files(session, monkeypatch, system):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    add_runs(system, 2)

    finalize_module.finalize(session)

    assert session.transcript.read_text().splitlines()[2] == "[00:00:00] at 0.0."
    assert soundfile.info(str(session.merged)).frames == 10 * RATE
    assert not system.audio_dir.exists()
    assert not system.partial.exists()
    assert not session.is_stopped()


def test_finalize_keeps_run_files_when_merge_check_fails(session, monkeypatch, system):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    monkeypatch.setattr(audio, "count_frames", lambda path: 0)
    add_runs(system, 2)

    with pytest.raises(audio.MergeFailed):
        finalize_module.finalize(session)

    assert len(system.run_paths()) == 2
    assert system.partial.exists()
    assert not session.merged.exists()


def test_finalize_after_merge_only_cleans_up(session, monkeypatch, system):
    def no_backend(name, model):
        raise AssertionError("must not transcribe again")

    add_runs(system, 1)
    session.merged.write_bytes(b"checked before")
    system.partial.write_text('{"header": {}}\n')
    monkeypatch.setattr(backends, "make", no_backend)

    finalize_module.finalize(session)

    assert session.merged.read_bytes() == b"checked before"
    assert not system.audio_dir.exists()


# --- two streams (system + mic) ---


@pytest.fixture
def mic(session: Session) -> Stream:
    stream = session.stream(MIC)
    stream.audio_dir.mkdir()
    return stream


def test_timeline_maps_wall_time_both_ways(system):
    add_runs(system, 2, gap_after=1, gap=10.0)  # audio 0-5 s at wall 0-5 s, audio 5-10 s at wall 15-20 s
    timeline = system.timeline()
    assert timeline.wall_at(2.0) == T0 + timedelta(seconds=2)
    assert timeline.wall_at(6.0) == T0 + timedelta(seconds=16)
    assert timeline.time_at(T0 - timedelta(seconds=1)) == 0.0
    assert timeline.time_at(T0 + timedelta(seconds=3)) == 3.0
    assert timeline.time_at(T0 + timedelta(seconds=8)) == 5.0  # in the gap: start of the next run
    assert timeline.time_at(T0 + timedelta(seconds=17)) == 7.0
    assert timeline.time_at(T0 + timedelta(seconds=22)) == 12.0  # after the end: not cut


def test_two_streams_give_labelled_lines_in_wall_time_order(session, system, mic):
    add_runs(system, 2)  # 10 s from T0
    write_run(mic, ramp(0, 4.0), T0 + timedelta(seconds=3))  # 4 s from T0 + 3 s
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)

    body = session.transcript.read_text().splitlines()[2:]
    assert body == [
        "[00:00:00] Them: at 0.0.",
        "[00:00:02] Them: at 2.0.",
        "[00:00:03] Me: at 0.0.",
        "[00:00:04] Them: at 4.0.",
        "[00:00:05] Me: at 2.0.",
        "[00:00:06] Them: at 6.0.",
        "[00:00:08] Them: at 8.0.",
    ]
    assert texts(mic) == ["at 0.0.", "at 2.0."]
    assert Progress(mic.partial, {}).end == pytest.approx(4.0)


def test_one_stream_has_no_labels(session, system):
    add_runs(system, 1)
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)
    assert session.transcript.read_text().splitlines()[2] == "[00:00:00] at 0.0."


def test_mic_gap_gets_own_line_unless_system_stopped_too(session, system, mic):
    add_runs(system, 2, gap_after=1, gap=30.0)  # system: wall 0-5 s, then 35-40 s
    write_run(mic, ramp(0, 1.0), T0)
    write_run(mic, ramp(1, 2.0), T0 + timedelta(seconds=2))  # mic only: 1 s gap at wall 1 s
    write_run(mic, ramp(3, 2.0), T0 + timedelta(seconds=35))  # both stopped
    session.mark_stopped()

    transcriber.run(session, FakeBackend(), poll=0)

    body = session.transcript.read_text()
    assert "[00:00:02] (Me: recording gap 00:00:01)\n" in body
    assert "[00:00:05] (recording gap 00:00:30)\n" in body
    assert "Me: recording gap 00:00:28" not in body


def test_caption_is_the_latest_held_words_with_label(session, system, mic):
    add_runs(system, 1)
    write_run(mic, ramp(0, 2.0), T0 + timedelta(seconds=2))
    states = [transcriber.StreamState(s, FakeBackend()) for s in (system, mic)]
    states[0].caption = console.Caption(1.0, "a", "b")
    states[1].caption = console.Caption(0.5, "c", "")  # mic time 0.5 s = system time 2.5 s
    caption = transcriber.Layout(states).caption()
    assert (caption.start, caption.agreed, caption.label) == (2.5, "c", "Me")
    for state in states:
        state.progress.close()


def aligned(system, mic, tmp_path):
    """write_aligned of system and mic into tmp_path. Return (frames, system data, mic data, mix data)."""
    frames = audio.write_aligned(
        [(system.timeline().files, tmp_path / "s.flac"), (mic.timeline().files, tmp_path / "m.flac")],
        tmp_path / "mix.flac",
    )
    read = [soundfile.read(str(tmp_path / n), dtype="float32", always_2d=True)[0] for n in ("s.flac", "m.flac", "mix.flac")]
    return (frames, *read)


def test_aligned_files_share_the_reference_time_axis(system, mic, tmp_path):
    write_run(system, np.full(10 * RATE, 0.1, np.float32), T0)
    write_run(mic, np.full(4 * RATE, 0.2, np.float32), T0 + timedelta(seconds=3))

    frames, sys_data, mic_data, mix_data = aligned(system, mic, tmp_path)

    assert frames == len(sys_data) == len(mic_data) == len(mix_data) == 10 * RATE
    assert np.allclose(sys_data, 0.1, atol=1e-6)
    assert np.allclose(mic_data[: 3 * RATE], 0.0) and np.allclose(mic_data[7 * RATE:], 0.0)
    assert np.allclose(mic_data[3 * RATE: 7 * RATE], 0.2, atol=1e-6)
    assert np.allclose(mix_data, sys_data + mic_data, atol=1e-6)


def test_aligned_mic_converts_rate_and_channels_and_follows_gaps(system, mic, tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "BLOCK_FRAMES", 10000)
    write_run(system, np.zeros((2 * 48000, 2), np.float32), T0, rate=48000)
    write_run(system, np.zeros((2 * 48000, 2), np.float32), T0 + timedelta(seconds=12), rate=48000)
    write_run(mic, np.full(14 * RATE, 0.5, np.float32), T0 - timedelta(seconds=1))

    _, _, mic_data, mix_data = aligned(system, mic, tmp_path)

    assert mic_data.shape == mix_data.shape == (4 * 48000, 2)
    assert np.allclose(mic_data[1000:2 * 48000 - 1000], 0.5, atol=1e-3)  # mic at wall 0-2 s
    assert np.allclose(mic_data[2 * 48000 + 1000: 3 * 48000 - 1000], 0.5, atol=1e-3)  # mic at wall 12-13 s
    assert np.allclose(mic_data[3 * 48000 + 1000:], 0.0, atol=1e-3)  # mic ended at wall 13 s
    assert np.allclose(mix_data, mic_data, atol=1e-6)


def test_aligned_mix_cuts_the_sum_to_full_scale(system, mic, tmp_path):
    write_run(system, np.full(RATE, 0.8, np.float32), T0)
    write_run(mic, np.full(RATE, 0.8, np.float32), T0)
    _, sys_data, mic_data, mix_data = aligned(system, mic, tmp_path)
    assert mix_data.max() <= 1.0 and mix_data.min() > 0.99
    assert np.allclose(sys_data, 0.8, atol=1e-6) and np.allclose(mic_data, 0.8, atol=1e-6)


def test_aligned_failed_check_renames_no_file(system, mic, tmp_path, monkeypatch):
    write_run(system, np.full(RATE, 0.1, np.float32), T0)
    write_run(mic, np.full(RATE, 0.1, np.float32), T0)
    monkeypatch.setattr(audio, "count_frames", lambda path: 0 if path.name == "m.flac.tmp" else RATE)
    with pytest.raises(audio.MergeFailed, match="m.flac.tmp"):
        aligned(system, mic, tmp_path)
    assert not any(p.suffix == ".flac" for p in tmp_path.iterdir())


def test_finalize_two_streams_writes_each_stream_and_the_mix(session, system, mic, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    add_runs(system, 2)
    write_run(mic, ramp(0, 4.0), T0 + timedelta(seconds=3))

    finalize_module.finalize(session)

    for path in (system.merged, mic.merged, session.merged):
        assert soundfile.info(str(path)).frames == 10 * RATE
    mic_data, _ = soundfile.read(str(mic.merged), dtype="float32")
    assert mic_data[3 * RATE + 1] * 1000 == pytest.approx(0.0, abs=1e-3)  # mic time 0 at system time 3 s
    assert "[00:00:03] Me: at 0.0." in session.transcript.read_text()
    assert not system.audio_dir.exists() and not mic.audio_dir.exists()
    assert not system.partial.exists() and not mic.partial.exists()


def test_finalize_waits_for_mic_recorder(session, system, mic):
    add_runs(system, 1)
    (session.folder / "recorder.mic.pid").write_text(str(os.getppid()))
    with pytest.raises(finalize_module.AlreadyRunning, match="mic recorder"):
        finalize_module.finalize(session)


def test_mic_writer_writes_to_mic_folder(mic):
    writer = RunWriter(mic, 1000, 1)
    writer.add(np.ones((500, 1), np.float32), arrived=T0.timestamp() + 0.5)
    writer.close()
    assert [p.parent.name for p in mic.run_paths()] == ["mic"]
