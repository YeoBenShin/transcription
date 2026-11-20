"""Command line interface: python -m live {start,finalize,devices}."""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from live import backends, console
from live.audio import MergeFailed
from live.finalize import finalize
from live.session import MIC, STREAMS, SYSTEM, AlreadyRunning, Session, recorder_role, running_pid

POLL_SECONDS = 0.5
TRANSCRIBER_EXIT_WAIT = 30.0
CHILD_RESTART_DELAY = 2.0
CHILD_RESTARTS = 3  # restarts in a row with no progress, then the child stays stopped
DEFAULT_DEVICE = "BlackHole"
MIC_WARNING = (
    "--mic records the microphone as it is. Use headphones: with speakers, the microphone also hears "
    "the others, and their lines show twice. Mute in the meeting app does not stop this recording"
)


def log(message: str) -> None:
    console.say(f"live: {message}")

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
    for stream in STREAMS:
        pid = running_pid(session, recorder_role(stream))
        if pid is not None:
            raise SystemExit(f"a {stream} recorder (pid {pid}) already runs for {session.folder}")
    session.clear_stopped()
    return session


def recorder_sources(args) -> dict:
    """Recorder arguments per stream. system: an input device, or a loopback of a playback device.
    mic (with --mic or --mic-only): the named input device, or the default input device.
    With --mic-only, there is no system stream."""
    if args.mic_only:
        return {MIC: ["--device", args.mic] if args.mic else []}
    if args.loopback:
        sources = {SYSTEM: ["--loopback"] + (["--device", args.device] if args.device else [])}
    else:
        sources = {SYSTEM: ["--device", args.device or DEFAULT_DEVICE]}
    if args.mic is not None:
        sources[MIC] = ["--device", args.mic] if args.mic else []
    return sources


class Child:
    """A child process (a recorder or the transcriber), started again CHILD_RESTART_DELAY after it exits.

    The parent calls check() in its poll loop while it records. During the recording, any exit is
    a failure: a child exits normally only when the parent closes its stdin, or (the transcriber)
    after the session is stopped and all audio is done.

    `progress()` is a number that grows when the child does work. A child that exits with no
    progress since its first start (for example, no device with that name, or a model that does
    not load) is not restarted. After CHILD_RESTARTS restarts in a row with no progress, it is not
    restarted again. A run with progress resets the count.
    """

    def __init__(self, name: str, module: str, args: list, progress: Callable[[], int]):
        self.name = name
        self.module = module
        self.args = args
        self.progress = progress
        self.worked = False
        self.failed_restarts = 0
        self.restart_at: Optional[float] = None
        self.process: Optional[subprocess.Popen] = None
        self.progress_at_start = 0
        self.start()

    def start(self) -> None:
        self.restart_at = None
        self.progress_at_start = self.progress()
        self.process = spawn(self.module, self.args)

    def check(self, now: float) -> bool:
        """Start a restart that is due, or plan one if the child exited. False: the child is stopped for good."""
        if self.restart_at is not None:
            if now >= self.restart_at:
                self.start()
            return True
        code = self.process.poll()
        if code is None:
            return True
        if self.progress() > self.progress_at_start:
            self.worked = True
            self.failed_restarts = 0
        elif self.worked:
            self.failed_restarts += 1
        if not self.worked:
            log(f"{self.name} stopped by itself (exit {code})")
            return False
        if self.failed_restarts >= CHILD_RESTARTS:
            log(f"{self.name} stopped by itself (exit {code}); {CHILD_RESTARTS} restarts did no work, no more restarts")
            return False
        try_number = f" (try {self.failed_restarts + 1}/{CHILD_RESTARTS})" if self.failed_restarts else ""
        log(f"{self.name} stopped by itself (exit {code}); restart in {CHILD_RESTART_DELAY:.0f} s{try_number}")
        self.restart_at = now + CHILD_RESTART_DELAY
        return True

    def start_planned(self) -> None:
        """Start a planned restart now, without the wait."""
        if self.restart_at is not None:
            self.start()

    def stop(self) -> None:
        """Close the stdin of a running child, so it finishes its work and exits. Cancel a planned restart."""
        self.restart_at = None
        if self.process.poll() is None:
            self.process.stdin.close()


def transcript_progress(session: Session) -> int:
    """Bytes of records in the progress files of all streams, after the header line. The transcriber
    appends records after each pass."""
    total = 0
    for stream in session.streams():
        try:
            with stream.partial.open("rb") as handle:
                header = len(handle.readline())
                total += handle.seek(0, 2) - header
        except FileNotFoundError:
            pass
    return total


def stop_on_signal(signum, frame):
    raise KeyboardInterrupt


def start(args) -> int:
    signal.signal(signal.SIGINT, stop_on_signal)
    signal.signal(signal.SIGTERM, stop_on_signal)
    session = open_session(args)
    folder = str(session.folder)
    sources = recorder_sources(args)
    if MIC in sources and SYSTEM in sources:
        log(MIC_WARNING)
    recorders = {
        stream: Child(
            f"{stream} recorder",
            "live.recorder",
            [folder, "--stream", stream, *source],
            lambda stream=stream: len(session.stream(stream).run_paths()),
        )
        for stream, source in sources.items()
    }
    transcriber = Child(
        "transcriber",
        "live.transcriber",
        [folder, "--backend", args.backend, "--model", args.model],
        lambda: transcript_progress(session),
    )
    log(f"session {folder}. Ctrl+C stops the recording")

    running = dict(recorders)
    transcriber_running = True
    try:
        while running:
            now = time.monotonic()
            if transcriber_running:
                transcriber_running = transcriber.check(now)
            for stream, recorder in list(running.items()):
                if not recorder.check(now):
                    del running[stream]
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log("stopping the recording")
        for recorder in recorders.values():
            recorder.stop()
        for recorder in recorders.values():
            wait_through_ctrl_c(recorder.process, "saving the last audio blocks")
    session.mark_stopped()
    process = transcriber.process
    if not session.has_audio():
        transcriber.stop()
        process.wait()
        log("no audio was recorded")
        return 1

    transcriber.start_planned()
    process = transcriber.process
    finish = f"python -m live finalize {folder}"
    try:
        if process.poll() is None:
            log("transcribing the audio that is left. Ctrl+C leaves it for later")
        code = process.wait()
    except KeyboardInterrupt:
        transcriber.stop()
        try:
            process.wait(timeout=TRANSCRIBER_EXIT_WAIT)
        except subprocess.TimeoutExpired:
            process.kill()
        log(f"transcript not complete. To finish it and merge the audio: {finish}")
        return 1
    if code != 0:
        log(f"transcriber failed (exit {code}). To finish: {finish}")
        return 1

    try:
        finalize(session)
    except (MergeFailed, AlreadyRunning) as error:
        log(f"{error}. The run files are kept. To try again: {finish}")
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
        print(f"{error}. The run files are kept.", file=sys.stderr)
        return 1
    log(f"done: {session.transcript} and {session.merged}")
    return 0


def devices_command(args) -> int:
    import sounddevice

    print("Input devices (--device):")
    for device in sounddevice.query_devices():
        if device["max_input_channels"] > 0:
            print(f"  {device['name']}  ({device['max_input_channels']} ch, {device['default_samplerate']:.0f} Hz)")
    if sys.platform == "win32":
        import pyaudiowpatch

        from live.recorder import loopback_devices

        audio = pyaudiowpatch.PyAudio()
        try:
            print("Loopback devices (--loopback --device):")
            for device in loopback_devices(audio):
                print(f"  {device['name']}  ({device['maxInputChannels']} ch, {device['defaultSampleRate']:.0f} Hz)")
        finally:
            audio.terminate()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m live", description="Live audio input -> transcript .txt and .flac.")
    commands = parser.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("start", help="record and transcribe until Ctrl+C")
    begin.add_argument(
        "--device",
        help=f"input device name, or part of it (default {DEFAULT_DEVICE}). With --loopback: playback device "
        "name (default: the default playback device)",
    )
    begin.add_argument(
        "--loopback", action="store_true", help="Windows: record what a playback device plays (WASAPI loopback)"
    )
    begin.add_argument(
        "--mic",
        nargs="?",
        const="",
        metavar="NAME",
        help="also record and transcribe a microphone: the input device whose name contains NAME, or the "
        "default input device. Lines get the label 'Me:' (microphone) or 'Them:' (system audio)",
    )
    begin.add_argument(
        "--mic-only",
        action="store_true",
        help="record and transcribe only a microphone (the one --mic names, else the default input device), "
        "no system audio. Lines have no label",
    )
    begin.add_argument("--label", default="", help="text added to the session folder name")
    begin.add_argument("--outdir", type=Path, default=Path("out"))
    begin.add_argument("--session", type=Path, help="continue this session folder")
    begin.add_argument("--backend", default=backends.DEFAULT_BACKEND, choices=backends.names())
    begin.add_argument("--model", default=backends.DEFAULT_MODEL)
    begin.set_defaults(handler=start)

    end = commands.add_parser("finalize", help="finish the transcript and merge the audio of a stopped session")
    end.add_argument("session", type=Path)
    end.add_argument("--backend", choices=backends.names())
    end.add_argument("--model")
    end.set_defaults(handler=finalize_command)

    listing = commands.add_parser("devices", help="list input devices (and loopback devices on Windows)")
    listing.set_defaults(handler=devices_command)

    args = parser.parse_args(argv)
    if getattr(args, "mic_only", False) and (args.loopback or args.device):
        parser.error("--mic-only records no system audio: --device and --loopback do not apply. Use --mic NAME")
    return args.handler(args)
