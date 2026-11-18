"""Read a span of the timeline for a backend, and merge all snippets into one file."""

from pathlib import Path
from typing import List, Tuple

import numpy as np
import soundfile
import soxr

from live.backends import SAMPLE_RATE
from live.session import LIVE_DTYPE, TMP_SUFFIX, Snippet, Timeline, sealed_path

SUBTYPE = "PCM_24"
VERIFY_BLOCK = 1 << 20


def read_frames(snippet: Snippet, first: int, last: int) -> np.ndarray:
    """Frames [first, last) of a snippet as float32 (frames x channels).

    A live snippet is read from its .pcm file. If the recorder replaced that file by its FLAC
    in the meantime, the same frames are read from the FLAC.
    """
    if snippet.live:
        try:
            with open(snippet.path, "rb") as handle:
                handle.seek(first * 4 * snippet.channels)
                raw = handle.read((last - first) * 4 * snippet.channels)
            data = np.frombuffer(raw, LIVE_DTYPE).reshape(-1, snippet.channels)
            if len(data) == last - first:
                return data.astype(np.float32)
        except FileNotFoundError:
            pass
        path = sealed_path(snippet.path)
    else:
        path = snippet.path
    data, _ = soundfile.read(str(path), start=first, stop=last, dtype="float32", always_2d=True)
    return data


def read_16k(timeline: Timeline, start: float, end: float) -> np.ndarray:
    """Audio from timeline time `start` to `end` as 16 kHz mono float32.

    Consecutive snippets with the same rate are resampled together, so the joins have no
    filter edge.
    """
    groups: List[Tuple[int, List[np.ndarray]]] = []
    for snippet, s0 in zip(timeline.snippets, timeline.starts):
        s1 = s0 + snippet.seconds
        if s1 <= start or s0 >= end:
            continue
        first = int(round((max(start, s0) - s0) * snippet.rate))
        last = int(round((min(end, s1) - s0) * snippet.rate))
        mono = read_frames(snippet, first, last).mean(axis=1)
        if groups and groups[-1][0] == snippet.rate:
            groups[-1][1].append(mono)
        else:
            groups.append((snippet.rate, [mono]))
    pieces = [soxr.resample(np.concatenate(parts), rate, SAMPLE_RATE) for rate, parts in groups]
    return np.concatenate(pieces).astype(np.float32) if pieces else np.zeros(0, np.float32)


def convert(data: np.ndarray, rate: int, target_rate: int, target_channels: int) -> np.ndarray:
    """Change data (frames x channels) to target_rate and target_channels."""
    if data.shape[1] != target_channels:
        mono = data.mean(axis=1, keepdims=True)
        data = np.repeat(mono, target_channels, axis=1)
    if rate != target_rate:
        data = soxr.resample(data, rate, target_rate)
    return data.astype(np.float32)


def count_frames(path: Path) -> int:
    """Decode the whole file and count its frames."""
    total = 0
    with soundfile.SoundFile(str(path)) as handle:
        while True:
            block = handle.read(VERIFY_BLOCK, dtype="float32")
            if not len(block):
                return total
            total += len(block)


class MergeFailed(Exception):
    pass


def merge(snippets: List[Snippet], target: Path) -> int:
    """Join snippets end to end into one FLAC file at `target`, and return its frame count.

    The rate and channels of the first snippet are used for all. The file is written as
    <target>.tmp, decoded again to check the frame count, then renamed to target.
    Raise MergeFailed if the count is not equal.
    """
    if not snippets:
        raise MergeFailed("no snippets to merge")
    rate, channels = snippets[0].rate, snippets[0].channels
    tmp = target.with_name(target.name + TMP_SUFFIX)
    written = 0
    with soundfile.SoundFile(
        str(tmp), "w", samplerate=rate, channels=channels, format="FLAC", subtype=SUBTYPE
    ) as out:
        for snippet in snippets:
            data, _ = soundfile.read(str(snippet.path), dtype="float32", always_2d=True)
            data = convert(data, snippet.rate, rate, channels)
            out.write(data)
            written += len(data)
    decoded = count_frames(tmp)
    if decoded != written or soundfile.info(str(tmp)).frames != written:
        raise MergeFailed(f"{tmp.name}: {decoded} frames decoded, {written} written")
    tmp.replace(target)
    return written
