"""Recorder process: input device -> one raw .pcm run file in <session>/system/.

python -m live.recorder <session folder> --device NAME
Appends each audio block to the run file as it arrives, so the transcriber can read it at
once. Stops when stdin closes (end of file).
"""

import argparse
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Optional

import numpy as np

from live import console
from live.session import PCM_DTYPE, AlreadyRunning, Session, claim, release, run_name

SILENCE_PEAK = 1e-4
SILENCE_WARN_SECONDS = 30.0


def log(message: str) -> None:
    console.say(f"rec: {message}")


class RunWriter:
    """Appends audio blocks to the run file of this recorder run, and flushes each one to the OS.

    The file is made at the first block. Its name has the next sequence and the wall time of
    the first frame: the arrival time of the first block minus its length.
    """

    def __init__(self, session: Session, rate: int, channels: int):
        self.session = session
        self.rate = rate
        self.channels = channels
        self.file: Optional[BinaryIO] = None
        self.path: Optional[Path] = None
        self.frames = 0
        self.silent_frames = 0
        self.warned_silence = False

    def add(self, block: np.ndarray, arrived: Optional[float] = None) -> None:
        if self.file is None:
            arrived = time.time() if arrived is None else arrived
            wall = datetime.fromtimestamp(arrived) - timedelta(seconds=len(block) / self.rate)
            self.path = self.session.audio_dir / run_name(self.session.next_seq(), wall, self.rate, self.channels)
            self.file = open(self.path, "wb")
        self.check_signal(block)
        self.file.write(np.ascontiguousarray(block, PCM_DTYPE).tobytes())
        self.file.flush()
        self.frames += len(block)

    def close(self) -> None:
        if self.file is not None:
            self.file.close()

    def check_signal(self, block: np.ndarray) -> None:
        if len(block) and float(np.abs(block).max()) > SILENCE_PEAK:
            if self.warned_silence:
                log("signal back")
            self.silent_frames = 0
            self.warned_silence = False
            return
        self.silent_frames += len(block)
        if not self.warned_silence and self.silent_frames >= SILENCE_WARN_SECONDS * self.rate:
            log(
                f"no signal for {SILENCE_WARN_SECONDS:.0f} s. Check that the app plays to the "
                "Multi-Output Device and that the terminal has microphone access"
            )
            self.warned_silence = True


def find_input_device(name: str) -> dict:
    """The first input device whose name contains `name` (case is ignored)."""
    import sounddevice

    devices = [d for d in sounddevice.query_devices() if d["max_input_channels"] > 0]
    for device in devices:
        if name.lower() in device["name"].lower():
            return device
    names = ", ".join(d["name"] for d in devices)
    raise SystemExit(f"no input device matches {name!r}. Input devices: {names}")


def wait_for_stdin_close(stop: threading.Event) -> None:
    """Set `stop` when stdin reaches end of file. Reads the raw file descriptor, which holds no lock at exit."""
    fd = sys.stdin.fileno()
    while os.read(fd, 4096):
        pass
    stop.set()


def record(session: Session, device_name: str, stop: threading.Event) -> None:
    """Record from the device until `stop` is set, then write the blocks that are left."""
    import sounddevice

    device = find_input_device(device_name)
    rate = int(device["default_samplerate"])
    channels = device["max_input_channels"]
    blocks: "queue.Queue" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status.input_overflow:
            blocks.put(("overflow", time.time()))
        blocks.put((indata.copy(), time.time()))

    writer = RunWriter(session, rate, channels)
    with sounddevice.InputStream(
        device=device["index"], channels=channels, samplerate=rate, dtype="float32", callback=callback
    ):
        log(f"recording {device['name']} ({rate} Hz, {channels} ch) to {session.audio_dir}")
        while not stop.is_set():
            drain(blocks, writer, timeout=0.2)
    drain(blocks, writer, timeout=0)
    writer.close()
    log(f"stopped. {writer.frames / rate:.1f} s saved in this run")


def drain(blocks: "queue.Queue", writer: RunWriter, timeout: float) -> None:
    """Give all queued blocks to the writer. Wait at most `timeout` for the first one."""
    try:
        item = blocks.get(timeout=timeout) if timeout else blocks.get_nowait()
    except queue.Empty:
        return
    while True:
        data, arrived = item
        if isinstance(data, str):
            log(f"input overflow at {datetime.fromtimestamp(arrived):%H:%M:%S}: some audio is lost")
        else:
            writer.add(data, arrived)
        try:
            item = blocks.get_nowait()
        except queue.Empty:
            return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m live.recorder")
    parser.add_argument("session", type=Path)
    parser.add_argument("--device", required=True)
    args = parser.parse_args(argv)

    session = Session(args.session)
    session.audio_dir.mkdir(parents=True, exist_ok=True)
    try:
        claim(session, "recorder")
    except AlreadyRunning as error:
        log(str(error))
        return 1
    stop = threading.Event()
    threading.Thread(target=wait_for_stdin_close, args=(stop,), daemon=True).start()
    try:
        record(session, args.device, stop)
    finally:
        release(session, "recorder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
