"""Read a span of the timeline for a backend, merge the run files of a stream into one FLAC file, and mix streams."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import numpy as np
import soundfile
import soxr

from live.backends import SAMPLE_RATE
from live.session import PCM_DTYPE, TMP_SUFFIX, RunFile, Timeline

SUBTYPE = "PCM_24"
BLOCK_FRAMES = 1 << 20
CONTINUE_SECONDS = 0.001  # a read this close to the end of the last read continues it


def read_frames(run: RunFile, first: int, last: int) -> np.ndarray:
    """Frames [first, last) of a run file as float32 (frames x channels)."""
    with open(run.path, "rb") as handle:
        handle.seek(first * run.frame_bytes)
        raw = handle.read((last - first) * run.frame_bytes)
    return np.frombuffer(raw, PCM_DTYPE).reshape(-1, run.channels).astype(np.float32)


def read_16k(timeline: Timeline, start: float, end: float) -> np.ndarray:
    """Audio from timeline time `start` to `end` as 16 kHz mono float32.

    Consecutive run files with the same rate are resampled together, so the joins have no
    filter edge.
    """
    groups: List[Tuple[int, List[np.ndarray]]] = []
    for run, s0 in zip(timeline.files, timeline.starts):
        s1 = s0 + run.seconds
        if s1 <= start or s0 >= end:
            continue
        first = int(round((max(start, s0) - s0) * run.rate))
        last = int(round((min(end, s1) - s0) * run.rate))
        mono = read_frames(run, first, last).mean(axis=1)
        if groups and groups[-1][0] == run.rate:
            groups[-1][1].append(mono)
        else:
            groups.append((run.rate, [mono]))
    pieces = [soxr.resample(np.concatenate(parts), rate, SAMPLE_RATE) for rate, parts in groups]
    return np.concatenate(pieces).astype(np.float32) if pieces else np.zeros(0, np.float32)


def converted_blocks(run: RunFile, target_rate: int, target_channels: int, first: int = 0):
    """Yield the frames of a run file from frame `first` in blocks, changed to target_rate and target_channels."""
    stream = None
    if run.rate != target_rate:
        stream = soxr.ResampleStream(run.rate, target_rate, target_channels, dtype="float32")
    for block_first in range(first, run.frames, BLOCK_FRAMES):
        last = min(block_first + BLOCK_FRAMES, run.frames)
        data = read_frames(run, block_first, last)
        if data.shape[1] != target_channels:
            data = np.repeat(data.mean(axis=1, keepdims=True), target_channels, axis=1)
        if stream is not None:
            data = stream.resample_chunk(data, last=last == run.frames)
        yield data


def count_frames(path: Path) -> int:
    """Decode the whole file and count its frames."""
    total = 0
    with soundfile.SoundFile(str(path)) as handle:
        while True:
            block = handle.read(BLOCK_FRAMES, dtype="float32")
            if not len(block):
                return total
            total += len(block)


class MergeFailed(Exception):
    pass


class CheckedFlac:
    """A FLAC file that is written as <target>.tmp, checked, and then renamed to target."""

    def __init__(self, target: Path, rate: int, channels: int):
        self.target = target
        self.tmp = target.with_name(target.name + TMP_SUFFIX)
        self.file = soundfile.SoundFile(
            str(self.tmp), "w", samplerate=rate, channels=channels, format="FLAC", subtype=SUBTYPE
        )
        self.written = 0

    def write(self, data: np.ndarray) -> None:
        self.file.write(data)
        self.written += len(data)

    def check(self) -> None:
        """Close the file and decode it again. Raise MergeFailed if the frame count is not the count written."""
        self.file.close()
        decoded = count_frames(self.tmp)
        if decoded != self.written or soundfile.info(str(self.tmp)).frames != self.written:
            raise MergeFailed(f"{self.tmp.name}: {decoded} frames decoded, {self.written} written")

    def commit(self) -> None:
        self.tmp.replace(self.target)


def write_checked(blocks: Iterator[np.ndarray], target: Path, rate: int, channels: int) -> int:
    """Write the blocks as one FLAC file at `target` (see CheckedFlac), and return its frame count."""
    out = CheckedFlac(target, rate, channels)
    for data in blocks:
        out.write(data)
    out.check()
    out.commit()
    return out.written


def timeline_blocks(files: List[RunFile], rate: int, channels: int) -> Iterator[Tuple[RunFile, int, np.ndarray]]:
    """Yield (run file, frame position in it at `rate`, block) for all run files end to end."""
    for run in files:
        position = 0
        for data in converted_blocks(run, rate, channels):
            yield run, position, data
            position += len(data)


def merge(files: List[RunFile], target: Path) -> int:
    """Join run files end to end into one FLAC file at `target`, and return its frame count.

    The rate and channels of the first file are used for all. The files are read in blocks,
    so memory does not grow with the length. The file is checked as in write_checked.
    """
    if not files:
        raise MergeFailed("no audio to merge")
    rate, channels = files[0].rate, files[0].channels
    blocks = (data for _, _, data in timeline_blocks(files, rate, channels))
    return write_checked(blocks, target, rate, channels)


def frames_from(files: List[RunFile], wall: datetime, rate: int, channels: int) -> Iterator[np.ndarray]:
    """Yield the audio of the run files from wall time `wall` on, at rate and channels, with no end.

    Wall time before a run file and between run files gives zeros, as does all time after the last one.
    """
    at = wall
    for run in files:
        run_end = run.wall + timedelta(seconds=run.seconds)
        if run_end <= at:
            continue
        silence = int(round((run.wall - at).total_seconds() * rate))
        if silence > 0:
            yield np.zeros((silence, channels), np.float32)
            at = run.wall
        first = int(round((at - run.wall).total_seconds() * run.rate))
        yield from converted_blocks(run, rate, channels, first)
        at = run_end
    while True:
        yield np.zeros((BLOCK_FRAMES, channels), np.float32)


class WallReader:
    """Reads the audio of a timeline by wall time, at a given rate and channels.

    Reads that follow on from the last one continue the same resample stream. Other reads
    start again at their wall time.
    """

    def __init__(self, files: List[RunFile], rate: int, channels: int):
        self.files = files
        self.rate = rate
        self.channels = channels
        self.source: Optional[Iterator[np.ndarray]] = None
        self.buffer = np.zeros((0, channels), np.float32)
        self.next_wall: Optional[datetime] = None

    def read(self, wall: datetime, count: int) -> np.ndarray:
        """`count` frames from wall time `wall`."""
        if self.next_wall is None or abs((wall - self.next_wall).total_seconds()) > CONTINUE_SECONDS:
            self.source = frames_from(self.files, wall, self.rate, self.channels)
            self.buffer = np.zeros((0, self.channels), np.float32)
        parts = [self.buffer]
        have = len(self.buffer)
        while have < count:
            data = next(self.source)
            parts.append(data)
            have += len(data)
        joined = np.concatenate(parts)
        self.buffer = joined[count:]
        self.next_wall = wall + timedelta(seconds=count / self.rate)
        return joined[:count]


def write_aligned(streams: List[Tuple[List[RunFile], Path]], mix_target: Path) -> int:
    """Write each stream and the mix of all streams as FLAC files on one time axis, in one pass.

    streams: (run files, target) per stream. The first stream is the reference: its run files
    end to end are the time axis, and its first run file gives the rate and channels of all
    files. For each reference block at wall time W, each other stream is read at W (see
    WallReader), with zeros where it has no audio. Each stream block goes to its own file; the
    sum of the blocks, cut to [-1, 1], goes to mix_target. Audio of other streams outside the
    reference run files is in no file.

    All files have the same frame count, which is returned. All are checked (see CheckedFlac)
    before any is renamed, and mix_target is renamed last. Raise MergeFailed if a check fails.
    """
    reference = streams[0][0]
    if not reference:
        raise MergeFailed("no audio to merge")
    rate, channels = reference[0].rate, reference[0].channels
    readers = [WallReader(files, rate, channels) for files, _ in streams[1:]]
    outs = [CheckedFlac(target, rate, channels) for _, target in streams]
    mixed = CheckedFlac(mix_target, rate, channels)
    for run, position, data in timeline_blocks(reference, rate, channels):
        wall = run.wall + timedelta(seconds=position / rate)
        blocks = [data] + [reader.read(wall, len(data)) for reader in readers]
        for out, block in zip(outs, blocks):
            out.write(block)
        mixed.write(np.clip(np.sum(blocks, axis=0), -1.0, 1.0))
    for out in outs + [mixed]:
        out.check()
    for out in outs + [mixed]:
        out.commit()
    return mixed.written
