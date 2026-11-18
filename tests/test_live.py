"""live/: snippet writing, timeline gaps, commit rules, the transcriber loop, merge and finalize.

No audio device and no Whisper. Snippets are 16 kHz mono FLAC files whose sample value is
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
from live.recorder import SnippetWriter, seal_live, write_snippet
from live.session import Session, live_name, sealed_path, snippet_name

RATE = 16000
T0 = datetime(2026, 11, 18, 14, 0, 0)


def ramp(start: float, seconds: float) -> np.ndarray:
    """Mono samples whose value is (timeline time / 1000)."""
    return ((start + np.arange(int(seconds * RATE)) / RATE) / 1000).astype(np.float32)


def add_snippets(session: Session, count: int, seconds: float = 5.0, gap_after: int = 0, gap: float = 0.0) -> None:
    """Add `count` contiguous snippets after the existing ones. Add a wall-clock gap after snippet `gap_after`."""
    timeline = session.timeline()
    start = timeline.total
    wall = T0 + timedelta(seconds=start)
    if timeline.gaps():
        wall += timedelta(seconds=sum(g.seconds for g in timeline.gaps()))
    for i in range(count):
        seq = session.next_seq()
        write_snippet(session.audio_dir / snippet_name(seq, wall), ramp(start, seconds), RATE)
        start += seconds
        wall += timedelta(seconds=seconds + (gap if seq == gap_after else 0.0))


def add_live(session: Session, seconds: float, extra_bytes: int = 0) -> Path:
    """Write the next snippet as a live .pcm file (ramp after the existing timeline)."""
    timeline = session.timeline()
    wall = T0 + timedelta(seconds=timeline.total)
    path = session.audio_dir / live_name(session.next_seq(), wall, RATE, 1)
    path.write_bytes(ramp(timeline.total, seconds).astype("<f4").tobytes() + b"x" * extra_bytes)
    return path


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


# --- recorder snippet writer ---

def test_writer_cuts_fixed_snippets_and_flushes_rest(session):
    writer = SnippetWriter(session, rate=1000, channels=2, snippet_seconds=2)
    for _ in range(9):
        writer.add(np.zeros((500, 2), np.float32), arrived=T0.timestamp() + 0.5)
    writer.flush()

    timeline = session.timeline()
    assert [s.frames for s in timeline.snippets] == [2000, 2000, 500]
    assert timeline.total == 4.5
    assert timeline.gaps() == []
    assert [p.name[:6] for p in session.snippet_paths()] == ["000001", "000002", "000003"]


def test_second_run_continues_sequence_and_shows_gap(session):
    first = SnippetWriter(session, 1000, 1, 2)
    first.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 2)
    second = SnippetWriter(session, 1000, 1, 2)
    second.add(np.zeros((2000, 1), np.float32), arrived=T0.timestamp() + 14)

    timeline = session.timeline()
    assert [s.seq for s in timeline.snippets] == [1, 2]
    [gap] = timeline.gaps()
    assert gap.at == 2.0
    assert gap.seconds == pytest.approx(10.0, abs=0.01)


def test_tmp_files_are_not_snippets(session):
    add_snippets(session, 1)
    (session.audio_dir / (snippet_name(2, T0) + ".tmp")).write_bytes(b"half")
    assert len(session.snippet_paths()) == 1
    assert session.next_seq() == 2


def test_writer_keeps_open_snippet_in_live_file(session):
    writer = SnippetWriter(session, rate=1000, channels=2, snippet_seconds=2)
    for _ in range(5):
        writer.add(np.ones((500, 2), np.float32), arrived=T0.timestamp() + 0.5)

    [live] = session.live_paths()
    assert live.name.startswith("000002-") and live.name.endswith("-1000x2.pcm")
    assert live.stat().st_size == 500 * 2 * 4
    timeline = session.timeline()
    assert [(s.seq, s.live, s.frames) for s in timeline.snippets] == [(1, False, 2000), (2, True, 500)]
    assert timeline.total == 2.5

    writer.flush()
    assert session.live_paths() == []
    assert [s.frames for s in session.timeline().snippets] == [2000, 500]


def test_writer_splits_block_across_snippets(session):
    writer = SnippetWriter(session, rate=1000, channels=1, snippet_seconds=1)
    writer.add(np.arange(2500, dtype=np.float32)[:, None] / 1e4, arrived=T0.timestamp())

    [first, second] = session.timeline().snippets[:2]
    live = session.timeline().snippets[2]
    assert (first.frames, second.frames, live.frames) == (1000, 1000, 500)
    assert np.fromfile(live.path, "<f4")[0] == pytest.approx(0.2)


def test_live_file_is_used_only_as_next_snippet(session):
    add_snippets(session, 1)
    path = add_live(session, 1.0)
    assert session.timeline().total == 6.0

    write_snippet(sealed_path(path), ramp(5.0, 1.0), RATE)  # FLAC written, .pcm not removed yet
    timeline = session.timeline()
    assert [s.live for s in timeline.snippets] == [False, False]
    assert timeline.total == 6.0

    sealed_path(path).unlink()
    path.rename(path.with_name("000003" + path.name[6:]))  # seq 2 missing from the listing
    assert session.timeline().total == 5.0


def test_live_file_counts_only_complete_frames(session):
    add_live(session, 1.0, extra_bytes=3)
    assert session.timeline().total == 1.0


def test_read_16k_reads_live_file_and_falls_back_to_flac(session):
    add_snippets(session, 1)
    path = add_live(session, 2.0)
    timeline = session.timeline()

    samples = audio.read_16k(timeline, 4.0, 7.0)
    assert len(samples) == 3 * RATE
    assert samples[-1] * 1000 == pytest.approx(7.0, abs=1e-3)

    write_snippet(sealed_path(path), ramp(5.0, 2.0), RATE)
    path.unlink()
    assert np.allclose(audio.read_16k(timeline, 4.0, 7.0), samples, atol=1e-6)


def test_seal_live_saves_left_live_file(session):
    add_snippets(session, 1)
    add_live(session, 1.5, extra_bytes=2)

    assert seal_live(session) == 1

    assert session.live_paths() == []
    assert [s.frames for s in session.timeline().snippets] == [5 * RATE, int(1.5 * RATE)]
    assert session.next_seq() == 3


def test_seal_live_removes_live_file_whose_flac_exists(session):
    path = add_live(session, 1.0)
    write_snippet(sealed_path(path), ramp(0.0, 1.0), RATE)

    assert seal_live(session) == 0
    assert session.live_paths() == []


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
    add_snippets(session, 3)
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)

    assert texts(session) == [f"at {s}.0" for s in range(0, 13, 2)]
    assert Progress(session.partial, {}).end == pytest.approx(15.0)
    body = session.transcript.read_text().splitlines()
    assert body[0] == f"{session.name}-live"
    assert body[2:] == [f"[00:00:{s:02d}] at {s}.0" for s in range(0, 13, 2)]


def test_live_pass_leaves_last_segment_for_next_pass(session):
    add_snippets(session, 2)
    backend = FakeBackend()
    assert not transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    assert texts(session) == ["at 0.0", "at 2.0", "at 4.0", "at 6.0"]

    add_snippets(session, 1)
    session.mark_stopped()
    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(session)[4:] == ["at 7.5", "at 9.5", "at 11.5", "at 13.5"]


def test_live_file_audio_is_transcribed(session):
    add_snippets(session, 1)
    add_live(session, 3.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    assert texts(session) == ["at 0.0", "at 2.0", "at 4.0"]


def test_live_pass_waits_for_step_of_new_audio(session):
    add_live(session, 4.0)
    backend = FakeBackend()
    polls = []

    def stop_after_polls():
        polls.append(1)
        return len(polls) > 5

    transcriber.run(session, backend, should_exit=stop_after_polls, poll=0)
    assert backend.calls == 1


def test_resume_after_crash_removes_cut_line_and_continues(session):
    add_snippets(session, 3)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)
    with session.partial.open("a") as handle:
        handle.write('{"start": 99, "en')
    session.mark_stopped()

    assert transcriber.run(session, FakeBackend(), poll=0)
    assert texts(session) == [f"at {s}.0" for s in range(0, 13, 2)] + ["at 13.5"]


def test_pass_stops_at_gap_and_commits_all_before_it(session):
    add_snippets(session, 2, gap_after=1, gap=30.0)
    backend = FakeBackend()
    transcriber.run(session, backend, should_exit=lambda: backend.calls >= 1, poll=0)

    records = Progress(session.partial, {}).records
    assert records[-1]["end"] == 5.0
    assert [r["text"] for r in records if r["text"]] == ["at 0.0", "at 2.0"]


def test_prompt_does_not_cross_gap(session):
    add_snippets(session, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    prompts = []

    class Recording(FakeBackend):
        def transcribe(self, samples, prompt):
            prompts.append(prompt)
            return super().transcribe(samples, prompt)

    transcriber.run(session, Recording(), poll=0)
    assert prompts == [None, None]


def test_gap_line_in_transcript(session):
    add_snippets(session, 2, gap_after=1, gap=30.0)
    session.mark_stopped()
    transcriber.run(session, FakeBackend(), poll=0)

    body = session.transcript.read_text()
    assert "[00:00:05] (recording gap 00:00:30)\n" in body


# --- merge and finalize ---

def test_merge_joins_snippets_exactly(session, tmp_path):
    add_snippets(session, 3, seconds=2.5)
    snippets = session.timeline().snippets
    target = tmp_path / "merged.flac"

    frames = audio.merge(snippets, target)

    data, rate = soundfile.read(str(target), dtype="float32")
    parts = [soundfile.read(str(s.path), dtype="float32")[0] for s in snippets]
    assert rate == RATE and frames == len(data) == 3 * int(2.5 * RATE)
    assert np.array_equal(data, np.concatenate(parts))


def test_merge_converts_other_rate_and_channels(session, tmp_path):
    add_snippets(session, 1, seconds=1.0)
    stereo = np.zeros((48000, 2), np.float32)
    write_snippet(session.audio_dir / snippet_name(2, T0 + timedelta(seconds=1)), stereo, 48000)

    frames = audio.merge(session.timeline().snippets, tmp_path / "m.flac")

    info = soundfile.info(str(tmp_path / "m.flac"))
    assert (info.samplerate, info.channels, info.frames) == (RATE, 1, frames)
    assert frames == pytest.approx(2 * RATE, abs=2)


def test_read_16k_spans_snippets(session):
    add_snippets(session, 2)
    samples = audio.read_16k(session.timeline(), 4.0, 6.0)
    assert len(samples) == 2 * RATE
    assert samples[0] * 1000 == pytest.approx(4.0, abs=1e-3)
    assert samples[-1] * 1000 == pytest.approx(6.0, abs=1e-3)


def test_finalize_writes_txt_and_flac_and_removes_work_files(session, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    add_snippets(session, 2)

    finalize_module.finalize(session)

    assert session.transcript.read_text().splitlines()[2] == "[00:00:00] at 0.0"
    assert soundfile.info(str(session.merged)).frames == 10 * RATE
    assert not session.audio_dir.exists()
    assert not session.partial.exists()
    assert not session.is_stopped()


def test_finalize_saves_left_live_file_before_merge(session, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    add_snippets(session, 1)
    add_live(session, 2.0)

    finalize_module.finalize(session)

    assert soundfile.info(str(session.merged)).frames == 7 * RATE
    assert not session.audio_dir.exists()


def test_finalize_keeps_snippets_when_merge_check_fails(session, monkeypatch):
    monkeypatch.setattr(backends, "make", lambda name, model: FakeBackend())
    monkeypatch.setattr(audio, "count_frames", lambda path: 0)
    add_snippets(session, 2)

    with pytest.raises(audio.MergeFailed):
        finalize_module.finalize(session)

    assert len(session.snippet_paths()) == 2
    assert session.partial.exists()
    assert not session.merged.exists()


def test_finalize_after_merge_only_cleans_up(session, monkeypatch):
    def no_backend(name, model):
        raise AssertionError("must not transcribe again")

    add_snippets(session, 1)
    session.merged.write_bytes(b"checked before")
    session.partial.write_text('{"header": {}}\n')
    monkeypatch.setattr(backends, "make", no_backend)

    finalize_module.finalize(session)

    assert session.merged.read_bytes() == b"checked before"
    assert not session.audio_dir.exists()
