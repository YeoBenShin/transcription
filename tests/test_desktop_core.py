"""Desktop app parts with no window: settings, child commands, history of transcripts, child processes."""

import os
import sys
import time
from pathlib import Path

import pytest

from desktop import commands, history, process
from desktop.models.catalog import find
from desktop.settings import Settings


# Settings

def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(model_id="whisper-base", output_dir=str(tmp_path / "out"), system_source="device",
                        system_device="BlackHole", mic_enabled=True, mic_device="USB", onboarded=True)
    settings.save(path)
    assert Settings.load(path) == settings


def test_missing_or_bad_settings_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path / "none.json") == Settings()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert Settings.load(bad) == Settings()


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"model_id": "whisper-tiny", "old_key": 1}')
    assert Settings.load(path).model_id == "whisper-tiny"


def test_default_system_source_per_platform():
    assert Settings.default_system_source("darwin") == "tap"
    assert Settings.default_system_source("win32") == "loopback"


# Commands

MODELS = Path("/models")


def settings(**changes) -> Settings:
    values = dict(model_id="whisper-base", output_dir="/out", system_source="tap", mic_enabled=False)
    values.update(changes)
    return Settings(**values)


def test_engine_arguments_come_from_the_model():
    assert commands.engine_args(find("whisper-base"), MODELS) == [
        "--backend", "faster-whisper", "--model", str(MODELS / "whisper-base")]
    assert commands.engine_args(find("parakeet-tdt-0.6b-v2"), MODELS) == [
        "--backend", "parakeet-mlx", "--model", str(MODELS / "parakeet-tdt-0.6b-v2")]


def test_live_command_per_source():
    def live(**changes):
        argv = commands.live_start(settings(**changes), MODELS)
        assert argv[:5] == [sys.executable, "-u", "-m", "live", "start"]
        assert "--stdin-stop" in argv and argv[argv.index("--outdir") + 1] == "/out"
        return argv

    assert "--tap" in live()
    assert live(system_source="device", system_device="BlackHole")[-2:] == ["--device", "BlackHole"]
    assert live(system_source="loopback", system_device="")[-1] == "--loopback"
    assert live(system_source="loopback", system_device="Speakers")[-3:] == ["--loopback", "--device", "Speakers"]
    with_mic = live(mic_enabled=True, mic_device="")
    assert with_mic[-2:] == ["--tap", "--mic"]
    named_mic = live(mic_enabled=True, mic_device="USB")
    assert named_mic[-3:] == ["--tap", "--mic", "USB"]
    mic_only = live(system_source="none", mic_enabled=True, mic_device="USB")
    assert mic_only[-3:] == ["--mic-only", "--mic", "USB"]


def test_live_command_needs_a_source():
    with pytest.raises(ValueError):
        commands.live_start(settings(system_source="none", mic_enabled=False), MODELS)


def test_file_folder_url_merge_finalize_commands():
    s = settings()
    engine = commands.engine_args(find("whisper-base"), MODELS)
    assert commands.transcribe_file(s, MODELS, "/a/b.m4a") == [
        sys.executable, "-u", "-m", "app", "/a/b.m4a", "--outdir", "/out", *engine]
    assert commands.transcribe_url(s, MODELS, "https://youtu.be/x") == [
        sys.executable, "-u", "-m", "app", "https://youtu.be/x", "--outdir", "/out", *engine]
    assert commands.transcribe_folder(s, MODELS, "/inbox") == [
        sys.executable, "-u", "-m", "app.batch", "--inbox", "/inbox", "--keep", "--outdir", "/out", *engine]
    assert commands.merge([("Them", "/a.txt"), ("", "/b.txt")], "call", "/out") == [
        sys.executable, "-u", "-m", "transcript_merge", "Them=/a.txt", "/b.txt", "--name", "call", "--outdir", "/out"]
    assert commands.finalize(s, MODELS, "/out/live/s1")[:6] == [
        sys.executable, "-u", "-m", "live", "finalize", "/out/live/s1"]


def test_child_environment_adds_source_root_and_events(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = commands.child_env({"PATH": "/bin"})
    assert env["LIVE_EVENTS"] == "1" and env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(commands.SOURCE_ROOT)


# History

def write(path: Path, header: str, lines: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_history_lists_file_and_live_transcripts_newest_first(tmp_path):
    old = write(tmp_path / "20260101-Author-Talk.txt", "20260101-Author-Talk-talk", ["[00:00:00] Hello there."])
    os.utime(old, (1_700_000_000, 1_700_000_000))
    session = tmp_path / "live" / "20261120-101500"
    write(session / "20261120-101500.txt", "20261120-101500-live", ["[00:00:01] Them: Good morning.", "[00:00:04] Me: Hi."])
    (session / "20261120-101500.flac").write_bytes(b"")
    (tmp_path / "temp").mkdir()
    write(tmp_path / "temp" / "work.txt", "x", [])
    items = history.scan(tmp_path)
    assert [i.kind for i in items] == ["live", "file"]
    live = items[0]
    assert live.title == "20261120-101500" and live.finalized and live.session == session
    assert live.preview.startswith("Them: Good morning. Me: Hi.")
    assert items[1].title == "20260101-Author-Talk"


def test_live_session_without_flac_is_not_finalized(tmp_path):
    session = tmp_path / "live" / "s1"
    write(session / "s1.txt", "s1-live", ["[00:00:01] Hi."])
    (session / "system").mkdir()
    [item] = history.scan(tmp_path)
    assert not item.finalized


def test_live_session_with_no_transcript_yet_is_listed(tmp_path):
    (tmp_path / "live" / "s2" / "mic").mkdir(parents=True)
    [item] = history.scan(tmp_path)
    assert item.kind == "live" and item.preview == "" and not item.finalized


def test_search_matches_text_case_insensitive(tmp_path):
    write(tmp_path / "a.txt", "a-x", ["[00:00:00] The budget review."])
    write(tmp_path / "b.txt", "b-x", ["[00:00:00] Hiring plan."])
    items = history.scan(tmp_path)
    assert [i.title for i in history.search(items, "BUDGET")] == ["a"]
    assert len(history.search(items, "")) == 2


def test_missing_output_folder_gives_no_items(tmp_path):
    assert history.scan(tmp_path / "none") == []


# Child process

def test_child_lines_split_on_newline_and_carriage_return():
    lines = []
    child = process.Child([sys.executable, "-c", "import sys; sys.stdout.write('a\\rb\\nc\\n'); sys.stdout.flush()"],
                          on_line=lines.append)
    assert child.wait(10) == 0
    assert lines == ["a", "b", "c"]


def test_child_close_stdin_lets_it_finish():
    lines = []
    child = process.Child([sys.executable, "-c", "import sys; sys.stdin.read(); print('bye')"], on_line=lines.append)
    child.close_stdin()
    assert child.wait(10) == 0
    assert lines == ["bye"]


def test_child_kill_stops_its_children_too(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time; "
        f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open(r'{marker}', 'w').write(str(p.pid)); time.sleep(60)"
    )
    child = process.Child([sys.executable, "-c", code], on_line=lambda line: None)
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    grandchild = int(marker.read_text())
    child.kill()
    child.wait(10)
    deadline = time.time() + 10
    while process.is_running(grandchild) and time.time() < deadline:
        time.sleep(0.1)
    assert not process.is_running(grandchild)


def test_on_exit_gets_the_exit_code():
    codes = []
    child = process.Child([sys.executable, "-c", "raise SystemExit(3)"], on_line=lambda l: None, on_exit=codes.append)
    child.wait(10)
    deadline = time.time() + 5
    while not codes and time.time() < deadline:
        time.sleep(0.05)
    assert codes == [3]
