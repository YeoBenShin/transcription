"""live/: run file writing, timeline gaps, commit rules, the transcriber loop, merge and finalize.

No audio device and no Whisper. Run files are 16 kHz mono .pcm files whose sample value is
the timeline time / 1000, so a fake backend can read where its audio comes from.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import soundfile

from live import audio, backends, finalize as finalize_module, transcriber
from live.backends import Segment
from live.progress import Progress
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
    """Returns a 1.5 s segment every 2 s of audio. Text is 'at <timeline time of its start>'."""

    name = "fake"
    model = "m"

    def __init__(self):
        self.calls = 0

    def transcribe(self, samples, prompt):
        self.calls += 1
        length = len(samples) / RATE
        segments = []
        k = 0
        while k * 2 + 1.5 <= length + 1e-9:
            at = samples[int(k * 2 * RATE)] * 1000
            segments.append(Segment(k * 2, k * 2 + 1.5, f"at {at:.1f}"))
            k += 1
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


# --- commit rules ---

def seg(start, end, text="x"):
    return Segment(start, end, text)


def test_choose_keeps_last_segment_open():
    records = transcriber.choose([seg(0, 2), seg(3, 9.5)], 10.0, 20.0, final=False, capped=False)
    assert records == [{"start": 10.0, "end": 12.0, "text": "x"}]


def test_choose_commits_last_segment_followed_by_silence():
    records = transcriber.choose([seg(0, 2), seg(3, 5)], 10.0, 20.0, final=False, capped=False)
    assert [r["end"] for r in records] == [12.0, 15.0]


def test_choose_final_commits_all_and_marks_span_done():
    records = transcriber.choose([seg(0, 2), seg(3, 9.9)], 10.0, 20.0, final=True, capped=False)
    assert [(r["end"], r["text"]) for r in records] == [(12.0, "x"), (19.9, "x"), (20.0, "")]


def test_choose_single_open_segment_commits_nothing():
    assert transcriber.choose([seg(0.0, 5.72)], 14.24, 20.0, final=False, capped=False) == []


def test_choose_silence_advances_but_keeps_tail():
    records = transcriber.choose([], 10.0, 20.0, final=False, capped=False)
    assert records == [{"start": 10.0, "end": 18.0, "text": ""}]


def test_choose_short_silence_does_not_go_back():
    assert transcriber.choose([], 10.0, 11.0, final=False, capped=False) == []


def test_choose_capped_single_segment_is_committed():
    records = transcriber.choose([seg(0, 30)], 0.0, 30.0, final=False, capped=True)
    assert records == [{"start": 0.0, "end": 30.0, "text": "x"}]


def test_choose_empty_text_segment_counts_as_silence():
    records = transcriber.choose([seg(0, 2, ""), seg(3, 9.5)], 10.0, 20.0, final=False, capped=False)
    assert records == [{"start": 10.0, "end": 12.0, "text": ""}]


# --- transcriber loop ---

def test_stopped_session_is_transcribed_to_the_end(session):
    add_runs(session, 3)
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)

    assert texts(session) == [f"at {s}.0" for s in range(0, 13, 2)]
    assert Progress(session.partial, {}).end == pytest.approx(15.0)
    body = session.transcript.read_text().splitlines()
    assert body[0] == f"{session.name}-live"
    assert body[2:] == [f"[00:00:{s:02d}] at {s}.0" for s in range(0, 13, 2)]


def test_live_pass_leaves_last_segment_for_next_pass(session):
    add_runs(session, 1, seconds=10.0)
    backend = FakeBackend()
    assert not transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    assert texts(session) == ["at 0.0", "at 2.0", "at 4.0", "at 6.0"]

    grow(session, 5.0)
    session.mark_stopped()
    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(session)[4:] == ["at 7.5", "at 9.5", "at 11.5", "at 13.5"]


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
    add_runs(session, 3)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    with session.partial.open("a") as handle:
        handle.write('{"start": 99, "en')
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(session) == [f"at {s}.0" for s in range(0, 13, 2)] + ["at 13.5"]


def test_pass_stops_at_gap_and_commits_all_before_it(session):
    add_runs(session, 2, gap_after=1, gap=30.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)

    records = Progress(session.partial, {}).records
    assert records[-1]["end"] == 5.0
    assert [r["text"] for r in records if r["text"]] == ["at 0.0", "at 2.0"]


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

    assert session.transcript.read_text().splitlines()[2] == "[00:00:00] at 0.0"
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
