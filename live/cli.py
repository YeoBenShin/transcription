"""Command line interface: python -m live {start,finalize,devices}."""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

from live import backends
from live.audio import MergeFailed
from live.finalize import finalize
from live.session import AlreadyRunning, Session, running_pid

POLL_SECONDS = 0.5
TRANSCRIBER_RESTARTS = 3
TRANSCRIBER_EXIT_WAIT = 30.0


def log(message: str) -> None:
    print(f"live: {message}", flush=True)


def spawn(module: str, args: list) -> subprocess.Popen:
    """Start `python -m module args` with a stdin pipe, in its own process group.

    Ctrl+C in the terminal then goes only to this process. Closing stdin tells the child to stop.
    """
    if sys.platform == "win32":
        group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    return subprocess.Popen([sys.executable, "-m", module, *args], stdin=subprocess.PIPE, **group)


def wait_through_ctrl_c(process: subprocess.Popen, message: str) -> int:
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            log(message)


def open_session(args) -> Session:
    if args.session is None:
        return Session.create(args.outdir, args.label)
    session = Session(args.session)
    if session.merged.exists():
        raise SystemExit(f"{session.folder} is finalized already. Start a new session.")
    pid = running_pid(session, "recorder")
    if pid is not None:
        raise SystemExit(f"a recorder (pid {pid}) already runs for {session.folder}")
    session.audio_dir.mkdir(parents=True, exist_ok=True)
    session.clear_stopped()
    return session


def stop_on_signal(signum, frame):
    raise KeyboardInterrupt


def start(args) -> int:
    signal.signal(signal.SIGINT, stop_on_signal)
    signal.signal(signal.SIGTERM, stop_on_signal)
    session = open_session(args)
    folder = str(session.folder)
    recorder = spawn(
        "live.recorder", [folder, "--device", args.device, "--snippet-seconds", str(args.snippet_seconds)]
    )
    transcriber_args = [folder, "--backend", args.backend, "--model", args.model]
    transcriber = spawn("live.transcriber", transcriber_args)
    restarts = 0
    log(f"session {folder}. Ctrl+C stops the recording")

    try:
        while recorder.poll() is None:
            code = transcriber.poll()
            if code not in (None, 0) and restarts < TRANSCRIBER_RESTARTS:
                restarts += 1
                log(f"transcriber stopped (exit {code}); restart {restarts}/{TRANSCRIBER_RESTARTS}")
                transcriber = spawn("live.transcriber", transcriber_args)
            time.sleep(POLL_SECONDS)
        log(f"recorder stopped by itself (exit {recorder.returncode})")
    except KeyboardInterrupt:
        log("stopping the recording")
        recorder.stdin.close()
        wait_through_ctrl_c(recorder, "saving the last snippet")
    session.mark_stopped()
    if not session.snippet_paths() + session.live_paths():
        transcriber.stdin.close()
        transcriber.wait()
        log("no audio was recorded")
        return 1

    finish = f"python -m live finalize {folder}"
    try:
        if transcriber.poll() is None:
            log("transcribing the audio that is left. Ctrl+C leaves it for later")
        code = transcriber.wait()
    except KeyboardInterrupt:
        transcriber.stdin.close()
        try:
            transcriber.wait(timeout=TRANSCRIBER_EXIT_WAIT)
        except subprocess.TimeoutExpired:
            transcriber.kill()
        log(f"transcript not complete. To finish it and merge the audio: {finish}")
        return 1
    if code != 0:
        log(f"transcriber failed (exit {code}). To finish: {finish}")
        return 1

    try:
        finalize(session)
    except (MergeFailed, AlreadyRunning) as error:
        log(f"{error}. The snippets are kept. To try again: {finish}")
        return 1
    log(f"done: {session.transcript} and {session.merged}")
    return 0


def finalize_command(args) -> int:
    session = Session(args.session)
    if not session.folder.is_dir():
        print(f"{session.folder} does not exist", file=sys.stderr)
        return 1
    try:
        finalize(session, args.backend, args.model)
    except (MergeFailed, AlreadyRunning) as error:
        print(f"{error}. The snippets are kept.", file=sys.stderr)
        return 1
    log(f"done: {session.transcript} and {session.merged}")
    return 0


def devices_command(args) -> int:
    import sounddevice

    for device in sounddevice.query_devices():
        if device["max_input_channels"] > 0:
            print(f"{device['name']}  ({device['max_input_channels']} ch, {device['default_samplerate']:.0f} Hz)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m live", description="Live audio input -> transcript .txt and .flac.")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("start", help="record and transcribe until Ctrl+C")
    begin.add_argument("--device", default="BlackHole", help="input device name, or part of it")
    begin.add_argument("--label", default="", help="text added to the session folder name")
    begin.add_argument("--outdir", type=Path, default=Path("out"))
    begin.add_argument("--session", type=Path, help="continue this session folder")
    begin.add_argument("--backend", default=backends.DEFAULT_BACKEND, choices=backends.names())
    begin.add_argument("--model", default=backends.DEFAULT_MODEL)
    begin.add_argument("--snippet-seconds", type=float, default=5.0)
    begin.set_defaults(handler=start)

    end = commands.add_parser("finalize", help="finish the transcript and merge the audio of a stopped session")
    end.add_argument("session", type=Path)
    end.add_argument("--backend", choices=backends.names())
    end.add_argument("--model")
    end.set_defaults(handler=finalize_command)

    listing = commands.add_parser("devices", help="list input devices")
    listing.set_defaults(handler=devices_command)

    args = parser.parse_args(argv)
    return args.handler(args)
