"""Recorder process: audio source -> one raw .pcm run file in <session>/<stream>/.

python -m live.recorder <session folder> [--stream system] --device NAME
python -m live.recorder <session folder> [--stream system] --loopback [--device NAME]
python -m live.recorder <session folder> --stream mic [--device NAME]
The source is an input device (sounddevice; for the mic stream the default input device if no
--device), or with --loopback the WASAPI loopback of a Windows playback device (pyaudiowpatch).
Appends each audio block to the run file as it arrives, so the transcriber can read it at once.
Stops when stdin closes (end of file).
"""

import argparse
import os
import queue
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Optional

import numpy as np

from live import console
from live.session import (
    MIC, PCM_DTYPE, STREAMS, SYSTEM, AlreadyRunning, Session, Stream, claim, recorder_role, release, run_name,
)

SILENCE_PEAK = 1e-4
SILENCE_WARN_SECONDS = 30.0
GAP_MIN_SECONDS = 0.2  # a shorter time between the run end and the next block is jitter, not a gap
IDLE_SECONDS = 0.5  # after this time with no block, the source is taken as silent


def log(message: str) -> None:
    console.say(f"rec: {message}")


class RunWriter:
    """Appends audio blocks to the run file of this recorder run, and flushes each one to the OS.

    The file is made at the first write. Its name has the next sequence and the wall time of
    the first frame: the arrival time of the first block minus its length.

    With fill_gaps, the writer adds zeros for the time in which the source sent no blocks
    (WASAPI loopback sends nothing while nothing plays). `anchor` is the wall time of frame 0,
    set again from each block that arrives, so the drift of the device clock does not add up.
    """

    def __init__(self, stream: Stream, rate: int, channels: int, fill_gaps: bool = False, silence_hint: str = ""):
        self.stream = stream
        self.rate = rate
        self.channels = channels
        self.fill_gaps = fill_gaps
        self.silence_hint = silence_hint
        self.anchor: Optional[float] = None
        self.file: Optional[BinaryIO] = None
        self.path: Optional[Path] = None
        self.frames = 0
        self.silent_frames = 0
        self.warned_silence = False

    def begin(self, now: float) -> None:
        """Set the wall time of frame 0 to the stream start, so silence from the start is filled."""
        if self.fill_gaps:
            self.anchor = now

    def add(self, block: np.ndarray, arrived: Optional[float] = None) -> None:
        arrived = time.time() if arrived is None else arrived
        start = arrived - len(block) / self.rate
        if self.fill_gaps:
            self.fill_to(start, GAP_MIN_SECONDS)
        self.write(block, start)
        if self.fill_gaps:
            self.anchor = arrived - self.frames / self.rate

    def idle(self, now: float) -> None:
        """Fill silence up to IDLE_SECONDS before `now`. Does nothing while blocks arrive."""
        if self.fill_gaps:
            self.fill_to(now - IDLE_SECONDS, 0.0)

    def fill_to(self, until: float, min_seconds: float) -> None:
        """Write zeros up to wall time `until`, if at least `min_seconds` of frames are missing."""
        if self.anchor is None:
            return
        missing = int(round((until - self.anchor) * self.rate)) - self.frames
        if missing > 0 and missing >= min_seconds * self.rate:
            self.write(np.zeros((missing, self.channels), np.float32), self.anchor + self.frames / self.rate)

    def write(self, block: np.ndarray, start: float) -> None:
        if self.file is None:
            self.path = self.stream.audio_dir / run_name(
                self.stream.next_seq(), datetime.fromtimestamp(start), self.rate, self.channels
            )
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
                log(f"{self.stream.name}: signal back")
            self.silent_frames = 0
            self.warned_silence = False
            return
        self.silent_frames += len(block)
        if not self.warned_silence and self.silent_frames >= SILENCE_WARN_SECONDS * self.rate:
            log(f"{self.stream.name}: no signal for {SILENCE_WARN_SECONDS:.0f} s. {self.silence_hint}".rstrip())
            self.warned_silence = True


def find_input_device(name: Optional[str]) -> dict:
    """The first input device whose name contains `name` (case is ignored), or the default input device if None."""
    import sounddevice

    if name is None:
        try:
            return sounddevice.query_devices(kind="input")
        except sounddevice.PortAudioError as error:
            raise SystemExit(f"no default input device: {error}")
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


@contextmanager
def input_stream(device_name: Optional[str], blocks: "queue.Queue", hint: str):
    """Record from an input device (sounddevice). Yields (name, rate, channels, silence hint)."""
    import sounddevice

    device = find_input_device(device_name)
    rate = int(device["default_samplerate"])
    channels = device["max_input_channels"]

    def callback(indata, frames, time_info, status):
        if status.input_overflow:
            blocks.put(("overflow", time.time()))
        blocks.put((indata.copy(), time.time()))

    with sounddevice.InputStream(
        device=device["index"], channels=channels, samplerate=rate, dtype="float32", callback=callback
    ):
        yield device["name"], rate, channels, hint


def loopback_devices(audio) -> list:
    return list(audio.get_loopback_device_info_generator())


def find_loopback_device(audio, name: Optional[str]) -> dict:
    """The loopback of the default playback device, or the first loopback whose name contains `name`."""
    if name is None:
        try:
            return audio.get_default_wasapi_loopback()
        except (LookupError, OSError) as error:
            raise SystemExit(f"no loopback for the default playback device: {error}")
    devices = loopback_devices(audio)
    for device in devices:
        if name.lower() in device["name"].lower():
            return device
    names = ", ".join(d["name"] for d in devices)
    raise SystemExit(f"no loopback device matches {name!r}. Loopback devices: {names}")


@contextmanager
def loopback_stream(device_name: Optional[str], blocks: "queue.Queue"):
    """Record what a Windows playback device plays (WASAPI loopback). Yields as input_stream."""
    if sys.platform != "win32":
        raise SystemExit("--loopback needs Windows (WASAPI). On macOS use BlackHole with --device")
    import pyaudiowpatch as pyaudio

    audio = pyaudio.PyAudio()
    try:
        device = find_loopback_device(audio, device_name)
        rate = int(device["defaultSampleRate"])
        channels = device["maxInputChannels"]

        def callback(data, frames, time_info, status):
            arrived = time.time()
            if status & pyaudio.paInputOverflow:
                blocks.put(("overflow", arrived))
            blocks.put((np.frombuffer(data, np.float32).reshape(-1, channels), arrived))
            return None, pyaudio.paContinue

        stream = audio.open(
            format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
            input_device_index=device["index"], stream_callback=callback,
        )
        try:
            playback = device["name"].replace("[Loopback]", "").strip()
            yield device["name"], rate, channels, f"Check that the app plays to {playback}"
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        audio.terminate()


SILENCE_HINTS = {
    SYSTEM: "Check that the app plays to the Multi-Output Device and that the terminal has microphone access",
    MIC: "Check that the microphone is not muted and that the terminal has microphone access",
}


def record(stream: Stream, device_name: Optional[str], loopback: bool, stop: threading.Event) -> None:
    """Record from the source until `stop` is set, then write the blocks that are left."""
    blocks: "queue.Queue" = queue.Queue()
    if loopback:
        source = loopback_stream(device_name, blocks)
    else:
        source = input_stream(device_name, blocks, SILENCE_HINTS[stream.name])
    with source as (name, rate, channels, hint):
        writer = RunWriter(stream, rate, channels, fill_gaps=loopback, silence_hint=hint)
        writer.begin(time.time())
        log(f"recording {name} ({rate} Hz, {channels} ch) to {stream.audio_dir}")
        while not stop.is_set():
            drain(blocks, writer, timeout=0.2)
            writer.idle(time.time())
    drain(blocks, writer, timeout=0)
    writer.close()
    log(f"{stream.name}: stopped. {writer.frames / rate:.1f} s saved in this run")


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
    parser.add_argument("--stream", default=SYSTEM, choices=STREAMS)
    parser.add_argument("--device", help="input device name, or with --loopback a playback device name (part of it)")
    parser.add_argument("--loopback", action="store_true", help="record a Windows playback device (WASAPI loopback)")
    args = parser.parse_args(argv)
    if args.stream == SYSTEM and not args.loopback and args.device is None:
        parser.error("--device is needed for the system stream without --loopback")

    session = Session(args.session)
    stream = session.stream(args.stream)
    stream.audio_dir.mkdir(parents=True, exist_ok=True)
    try:
        claim(session, recorder_role(stream.name))
    except AlreadyRunning as error:
        log(str(error))
        return 1
    stop = threading.Event()
    threading.Thread(target=wait_for_stdin_close, args=(stop,), daemon=True).start()
    try:
        record(stream, args.device, args.loopback, stop)
    finally:
        release(session, recorder_role(stream.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
