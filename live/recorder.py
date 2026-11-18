"""Recorder process: input device -> FLAC snippets in <session>/system/.

python -m live.recorder <session folder> --device NAME [--snippet-seconds 5]
Stops when stdin closes (end of file).
The open snippet is also written block by block to a raw .pcm file, so the transcriber can
read it before the snippet is complete.
"""

import argparse
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, List, Optional

import numpy as np
import soundfile

from live.session import (
    LIVE_DTYPE,
    TMP_SUFFIX,
    AlreadyRunning,
    Session,
    claim,
    live_name,
    live_snippet,
    release,
    sealed_path,
    snippet_name,
)

SUBTYPE = "PCM_24"
SILENCE_PEAK = 1e-4
SILENCE_WARN_SECONDS = 30.0
STALE_TRIES_AT_STOP = 20
STALE_WAIT_SECONDS = 0.05


def log(message: str) -> None:
    print(f"rec: {message}", flush=True)


def write_snippet(path: Path, data: np.ndarray, rate: int) -> None:
    """Write data as FLAC to <path>.tmp, then rename it to path."""
    tmp = path.with_name(path.name + TMP_SUFFIX)
    soundfile.write(str(tmp), data, rate, format="FLAC", subtype=SUBTYPE)
    tmp.replace(path)


def seal_live(session: Session) -> int:
    """Replace each live .pcm file left by a stopped recorder with its FLAC snippet. Return the count.

    Only complete frames are kept. A .pcm file whose FLAC exists already is removed.
    Call only when no recorder runs for the session.
    """
    sealed = 0
    for path in session.live_paths():
        target = sealed_path(path)
        snippet = live_snippet(path)
        if not target.exists() and snippet.frames:
            count = snippet.frames * snippet.channels
            data = np.fromfile(path, LIVE_DTYPE, count=count).reshape(snippet.frames, snippet.channels)
            write_snippet(target, data, snippet.rate)
            sealed += 1
        path.unlink()
    return sealed


class SnippetWriter:
    """Collects audio blocks and writes one snippet each `snippet_seconds`.

    The wall time in each name is the run start plus the frames written before it, so the
    snippets of one run join with no gap on the timeline. The frames of the open snippet are
    also appended to its live .pcm file as they arrive. The FLAC is written first, then the
    .pcm file is removed, so the frames are always in one of the two. If the removal fails
    because another process has the file open (Windows), it is tried again with each new block.
    """

    def __init__(self, session: Session, rate: int, channels: int, snippet_seconds: float):
        self.session = session
        self.rate = rate
        self.channels = channels
        self.snippet_frames = int(snippet_seconds * rate)
        self.seq = session.next_seq()
        self.run_start: Optional[datetime] = None
        self.frames_done = 0
        self.blocks: List[np.ndarray] = []
        self.buffered = 0
        self.live: Optional[BinaryIO] = None
        self.live_path: Optional[Path] = None
        self.stale: List[Path] = []  # live files of written snippets, not removed yet
        self.silent_frames = 0
        self.warned_silence = False

    def add(self, block: np.ndarray, arrived: Optional[float] = None) -> None:
        if self.run_start is None:
            arrived = time.time() if arrived is None else arrived
            self.run_start = datetime.fromtimestamp(arrived) - timedelta(seconds=len(block) / self.rate)
        self.check_signal(block)
        self.remove_stale()
        while len(block):
            part = block[: self.snippet_frames - self.buffered]
            block = block[len(part):]
            self.append_live(part)
            self.blocks.append(part)
            self.buffered += len(part)
            if self.buffered == self.snippet_frames:
                self.write(np.concatenate(self.blocks))

    def flush(self) -> None:
        """Write the frames that are left as a last, shorter snippet."""
        if self.buffered:
            self.write(np.concatenate(self.blocks))
        for _ in range(STALE_TRIES_AT_STOP):
            if not self.remove_stale():
                return
            time.sleep(STALE_WAIT_SECONDS)
        log(f"could not remove {len(self.stale)} live file(s); the next start or finalize removes them")

    @property
    def wall(self) -> datetime:
        """Wall time of the first frame of the open snippet."""
        return self.run_start + timedelta(seconds=self.frames_done / self.rate)

    def append_live(self, part: np.ndarray) -> None:
        """Append frames to the live .pcm file of the open snippet, and flush them to the OS."""
        if self.live is None:
            self.live_path = self.session.audio_dir / live_name(self.seq, self.wall, self.rate, self.channels)
            self.live = open(self.live_path, "wb")
        self.live.write(np.ascontiguousarray(part, LIVE_DTYPE).tobytes())
        self.live.flush()

    def write(self, data: np.ndarray) -> None:
        """Write the open snippet as FLAC, then remove its live file."""
        write_snippet(self.session.audio_dir / snippet_name(self.seq, self.wall), data, self.rate)
        if self.live is not None:
            self.live.close()
            self.stale.append(self.live_path)
            self.live = None
            self.remove_stale()
        self.seq += 1
        self.frames_done += len(data)
        self.blocks = []
        self.buffered = 0

    def remove_stale(self) -> int:
        """Try to remove each stale live file. Return the count that is left."""
        left = []
        for path in self.stale:
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                left.append(path)
        self.stale = left
        return len(left)

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


def record(session: Session, device_name: str, snippet_seconds: float, stop: threading.Event) -> None:
    """Record from the device until `stop` is set, then write the last snippet."""
    import sounddevice

    device = find_input_device(device_name)
    rate = int(device["default_samplerate"])
    channels = device["max_input_channels"]
    blocks: "queue.Queue" = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status.input_overflow:
            blocks.put(("overflow", time.time()))
        blocks.put((indata.copy(), time.time()))

    writer = SnippetWriter(session, rate, channels, snippet_seconds)
    with sounddevice.InputStream(
        device=device["index"], channels=channels, samplerate=rate, dtype="float32", callback=callback
    ):
        log(f"recording {device['name']} ({rate} Hz, {channels} ch) to {session.audio_dir}")
        while not stop.is_set():
            drain(blocks, writer, timeout=0.2)
    drain(blocks, writer, timeout=0)
    writer.flush()
    log(f"stopped. {writer.frames_done / rate:.1f} s saved in this run")


def drain(blocks: "queue.Queue", writer: SnippetWriter, timeout: float) -> None:
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
    parser.add_argument("--snippet-seconds", type=float, default=5.0)
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
        sealed = seal_live(session)
        if sealed:
            log(f"saved {sealed} live file(s) left by an earlier recorder as snippets")
        record(session, args.device, args.snippet_seconds, stop)
    finally:
        release(session, "recorder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
