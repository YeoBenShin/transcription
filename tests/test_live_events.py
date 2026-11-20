"""Control of `live start` by another program: stop by closing stdin, and JSON event lines on stdout
(LIVE_EVENTS=1). No audio device, no model."""

import json
import sys
import types
from argparse import Namespace
from pathlib import Path

import pytest

from live import cli, console


@pytest.fixture
def events(monkeypatch, capsys):
    monkeypatch.setenv(console.EVENTS_ENV, "1")

    def read():
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]

    return read


def test_say_is_a_log_event(events):
    console.say("hello")
    assert events() == [{"event": "log", "text": "hello"}]


def test_line_is_a_line_event(events):
    console.line(65.0, "Them: hi")
    assert events() == [{"event": "line", "start": 65.0, "text": "Them: hi"}]


def test_draft_is_a_draft_event_and_none_clears_it(events):
    draft = console.Draft()
    draft.show(console.Caption(3.0, "one two", "three", "Me"))
    draft.show(None)
    assert events() == [
        {"event": "draft", "start": 3.0, "agreed": "one two", "tail": "three", "label": "Me"},
        {"event": "draft", "start": None, "agreed": "", "tail": "", "label": ""},
    ]


def test_event_prints_nothing_without_events_mode(monkeypatch, capsys):
    monkeypatch.delenv(console.EVENTS_ENV, raising=False)
    console.event("session", folder="x")
    assert capsys.readouterr().out == ""


def test_line_without_events_mode_is_a_fixed_line(monkeypatch, capsys):
    monkeypatch.delenv(console.EVENTS_ENV, raising=False)
    console.line(65.0, "hi")
    assert capsys.readouterr().out == "[00:01:05] hi\n"


class FakeProcess:
    def __init__(self):
        self.stdin = types.SimpleNamespace(closed=False)
        self.stdin.close = lambda: setattr(self.stdin, "closed", True)
        self.code = None

    def poll(self):
        return 0 if self.stdin.closed else None

    def wait(self, timeout=None):
        return 0


def test_stdin_stop_stops_the_recording(monkeypatch, tmp_path, events):
    started = []

    def spawn(module, args):
        started.append((module, FakeProcess()))
        return started[-1][1]

    monkeypatch.setattr(cli, "spawn", spawn)
    monkeypatch.setattr(cli, "stdin_closed", lambda: (lambda: True))
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    args = Namespace(session=None, outdir=tmp_path, label="t", mic=None, mic_only=True, loopback=False, device=None,
                     tap=False, backend="faster-whisper", model="base", stdin_stop=True)
    assert cli.start(args) == 1  # no audio was recorded
    recorder = next(p for m, p in started if m == "live.recorder")
    assert recorder.stdin.closed
    seen = events()
    folder = next(e["folder"] for e in seen if e["event"] == "session")
    assert Path(folder).parent == tmp_path / "live"
    states = [e["state"] for e in seen if e["event"] == "state"]
    assert states == ["recording", "stopping", "failed"]


FAKE_TAP = '''
import sys, struct, time
args = sys.argv[1:]
if args == ["--status"]:
    print("granted"); sys.exit(0)
sys.stdout.buffer.write(b'{"rate": 8000, "channels": 2}\\n')
frames = [struct.pack("<2f", i / 1000, -i / 1000) for i in range(1000)]
data = b"".join(frames)
for start in range(0, len(data), 333):  # pieces that cut frames in half
    sys.stdout.buffer.write(data[start:start + 333]); sys.stdout.buffer.flush()
sys.stdin.read()
'''


def test_tap_stream_reads_header_and_whole_frames(tmp_path):
    import queue
    import time

    import numpy as np

    from live import recorder

    helper = tmp_path / "fake_tap.py"
    helper.write_text(FAKE_TAP)
    blocks = queue.Queue()
    with recorder.tap_stream(blocks, command=[sys.executable, str(helper)]) as (name, rate, channels, hint):
        assert (rate, channels) == (8000, 2)
        received = []
        deadline = time.time() + 5
        while sum(len(b) for b in received) < 1000 and time.time() < deadline:
            try:
                data, arrived = blocks.get(timeout=0.2)
                received.append(data)
            except queue.Empty:
                pass
    audio = np.concatenate(received)
    assert audio.shape == (1000, 2)
    assert np.allclose(audio[:, 0], np.arange(1000) / 1000)


def test_tap_stream_reports_helper_error(tmp_path):
    import queue

    from live import recorder

    helper = tmp_path / "bad_tap.py"
    helper.write_text("import sys; sys.stderr.write('permission is denied'); sys.exit(4)")
    with pytest.raises(SystemExit, match="permission is denied"):
        with recorder.tap_stream(queue.Queue(), command=[sys.executable, str(helper)]):
            pass


def test_tap_source_is_passed_to_the_system_recorder():
    from live.cli import recorder_sources

    args = Namespace(loopback=False, device=None, mic="", mic_only=False, tap=True)
    assert recorder_sources(args) == {"system": ["--tap"], "mic": []}
