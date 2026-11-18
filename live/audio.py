"""Read a span of the timeline for a backend, and merge all run files into one FLAC file."""

from pathlib import Path
from typing import List, Tuple

import numpy as np
import soundfile
import soxr

from live.backends import SAMPLE_RATE
from live.session import PCM_DTYPE, TMP_SUFFIX, RunFile, Timeline

SUBTYPE = "PCM_24"
BLOCK_FRAMES = 1 << 20


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


def converted_blocks(run: RunFile, target_rate: int, target_channels: int):
    """Yield the frames of a run file in blocks, changed to target_rate and target_channels."""
    stream = None
    if run.rate != target_rate:
        stream = soxr.ResampleStream(run.rate, target_rate, target_channels, dtype="float32")
    for first in range(0, run.frames, BLOCK_FRAMES):
        last = min(first + BLOCK_FRAMES, run.frames)
        data = read_frames(run, first, last)
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


def merge(files: List[RunFile], target: Path) -> int:
    """Join run files end to end into one FLAC file at `target`, and return its frame count.

    The rate and channels of the first file are used for all. The files are read in blocks,
    so memory does not grow with the length. The result is written as <target>.tmp, decoded
    again to check the frame count, then renamed to target. Raise MergeFailed if the count is
    not equal.
    """
    if not files:
        raise MergeFailed("no audio to merge")
    rate, channels = files[0].rate, files[0].channels
    tmp = target.with_name(target.name + TMP_SUFFIX)
    written = 0
    with soundfile.SoundFile(
        str(tmp), "w", samplerate=rate, channels=channels, format="FLAC", subtype=SUBTYPE
    ) as out:
        for run in files:
            for data in converted_blocks(run, rate, channels):
                out.write(data)
                written += len(data)
    decoded = count_frames(tmp)
    if decoded != written or soundfile.info(str(tmp)).frames != written:
        raise MergeFailed(f"{tmp.name}: {decoded} frames decoded, {written} written")
    tmp.replace(target)
    return written
