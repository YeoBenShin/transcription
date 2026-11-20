"""Session folder layout, streams, run file names, the audio timeline, and process claims."""

import os
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from app.models import safe_name

LIVE_DIRNAME = "live"
SYSTEM = "system"
MIC = "mic"
STREAMS = (SYSTEM, MIC)  # the first stream with audio is the time reference of the transcript
LABELS = {SYSTEM: "Them", MIC: "Me"}
SOURCE_ID = "live"
MERGED_SUFFIX = ".flac"
RUN_SUFFIX = ".pcm"
PCM_DTYPE = "<f4"  # raw float32 little-endian, channels interleaved, no header
PCM_BYTES = 4
TMP_SUFFIX = ".tmp"
WALL_FORMAT = "%Y%m%dT%H%M%S.%f"
GAP_MIN_SECONDS = 1.0
_RUN = re.compile(r"^(\d{6})-(\d{8}T\d{6}\.\d{3})-(\d+)x(\d+)\.pcm$")


def run_name(seq: int, wall: datetime, rate: int, channels: int) -> str:
    """'<seq 6 digits>-<YYYYMMDDTHHMMSS.mmm>-<rate>x<channels>.pcm'. wall is the time of the first sample."""
    return f"{seq:06d}-{wall.strftime(WALL_FORMAT)[:-3]}-{rate}x{channels}{RUN_SUFFIX}"


@dataclass
class RunFile:
    """The audio of one recorder run: a raw .pcm file that the recorder appends to while it runs."""

    path: Path
    seq: int
    wall: datetime  # time of the first sample
    frames: int  # complete frames at the time of the listing
    rate: int
    channels: int

    @property
    def seconds(self) -> float:
        return self.frames / self.rate

    @property
    def frame_bytes(self) -> int:
        return PCM_BYTES * self.channels


def run_file(path: Path) -> RunFile:
    """The run file at `path`, with the complete frames it holds now (0 if it is gone)."""
    match = _RUN.match(path.name)
    rate, channels = int(match.group(3)), int(match.group(4))
    try:
        frames = path.stat().st_size // (PCM_BYTES * channels)
    except FileNotFoundError:
        frames = 0
    wall = datetime.strptime(match.group(2), WALL_FORMAT)
    return RunFile(path, int(match.group(1)), wall, frames, rate, channels)


@dataclass
class Gap:
    at: float  # audio time where the gap is
    seconds: float  # wall-clock length of the missing audio


class Timeline:
    """Run files with audio, in sequence order, placed end to end on one audio time axis."""

    def __init__(self, files: List[RunFile]):
        self.files = files
        self.starts: List[float] = []
        total = 0.0
        for run in files:
            self.starts.append(total)
            total += run.seconds
        self.total = total

    def wall_at(self, at: float) -> datetime:
        """Wall time of timeline time `at`: the wall time of its run file plus the offset in it."""
        index = max(bisect_right(self.starts, at) - 1, 0)
        return self.files[index].wall + timedelta(seconds=at - self.starts[index])

    def time_at(self, wall: datetime) -> float:
        """Timeline time of wall time `wall`.

        Before the first run file: 0. In a gap between run files: the start of the next one.
        After the last run file: its start plus the offset, not cut at its end.
        """
        if not self.files:
            return 0.0
        for run, start in zip(self.files, self.starts):
            offset = (wall - run.wall).total_seconds()
            if offset < 0:
                return start
            if offset < run.seconds:
                return start + offset
        return self.starts[-1] + (wall - self.files[-1].wall).total_seconds()

    def gaps(self) -> List[Gap]:
        """Places where the wall clock jumps forward by GAP_MIN_SECONDS or more between run files."""
        result = []
        for before, after, start in zip(self.files, self.files[1:], self.starts[1:]):
            expected = before.wall + timedelta(seconds=before.seconds)
            missing = (after.wall - expected).total_seconds()
            if missing >= GAP_MIN_SECONDS:
                result.append(Gap(start, missing))
        return result


class Stream:
    """One audio source of a session: run files in <session>/<name>/, progress in <session>/transcript.<name>.partial.jsonl."""

    def __init__(self, session: "Session", name: str):
        self.session = session
        self.name = name

    @property
    def label(self) -> str:
        return LABELS[self.name]

    @property
    def audio_dir(self) -> Path:
        return self.session.folder / self.name

    @property
    def partial(self) -> Path:
        return self.session.folder / f"transcript.{self.name}.partial.jsonl"

    @property
    def merged(self) -> Path:
        """The FLAC file of this stream alone, made by finalize when the session has more than one stream."""
        return self.session.folder / f"{self.session.name}.{self.name}{MERGED_SUFFIX}"

    def run_paths(self) -> List[Path]:
        """Run files, in sequence order."""
        if not self.audio_dir.is_dir():
            return []
        return sorted(p for p in self.audio_dir.iterdir() if _RUN.match(p.name))

    def next_seq(self) -> int:
        paths = self.run_paths()
        return int(paths[-1].name[:6]) + 1 if paths else 1

    def timeline(self) -> Timeline:
        """The run files that hold at least 1 frame now, as a timeline."""
        files = [run_file(path) for path in self.run_paths()]
        return Timeline([f for f in files if f.frames])


class Session:
    """One live recording: <outdir>/live/<name>/, with one folder per stream."""

    def __init__(self, folder: Path):
        self.folder = folder

    @classmethod
    def create(cls, outdir: Path, label: str, now: Optional[datetime] = None) -> "Session":
        """Make <outdir>/live/<YYYYMMDD-HHMMSS[-label]>/ and return it."""
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        clean_label = safe_name(label)
        name = f"{stamp}-{clean_label}" if clean_label else stamp
        session = cls(outdir / LIVE_DIRNAME / name)
        session.folder.mkdir(parents=True, exist_ok=True)
        return session

    @property
    def name(self) -> str:
        return self.folder.name

    def stream(self, name: str) -> Stream:
        return Stream(self, name)

    def streams(self) -> List[Stream]:
        """The streams that have a folder, in STREAMS order."""
        return [Stream(self, name) for name in STREAMS if (self.folder / name).is_dir()]

    def has_audio(self) -> bool:
        return any(stream.timeline().files for stream in self.streams())

    @property
    def transcript(self) -> Path:
        return self.folder / f"{self.name}.txt"

    @property
    def merged(self) -> Path:
        return self.folder / f"{self.name}{MERGED_SUFFIX}"

    @property
    def stopped_marker(self) -> Path:
        return self.folder / "stopped"

    def is_stopped(self) -> bool:
        return self.stopped_marker.exists()

    def mark_stopped(self) -> None:
        self.stopped_marker.touch()

    def clear_stopped(self) -> None:
        self.stopped_marker.unlink(missing_ok=True)


def recorder_role(stream: str) -> str:
    """The pid file role of the recorder of `stream`."""
    return f"recorder.{stream}"


class AlreadyRunning(Exception):
    pass


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(session: Session, role: str) -> Optional[int]:
    """PID in <session>/<role>.pid if that process is alive, else None."""
    try:
        pid = int((session.folder / f"{role}.pid").read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return pid if pid != os.getpid() and pid_alive(pid) else None


def claim(session: Session, role: str) -> Path:
    """Write <session>/<role>.pid with this process id. Raise AlreadyRunning if another live process has it."""
    other = running_pid(session, role)
    if other is not None:
        raise AlreadyRunning(f"a {role} (pid {other}) already runs for {session.folder}")
    path = session.folder / f"{role}.pid"
    path.write_text(str(os.getpid()))
    return path


def release(session: Session, role: str) -> None:
    path = session.folder / f"{role}.pid"
    try:
        if int(path.read_text().strip()) == os.getpid():
            path.unlink()
    except (FileNotFoundError, ValueError):
        pass
