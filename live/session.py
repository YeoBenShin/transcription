"""Session folder layout, snippet names, the audio timeline, and process claims."""

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import soundfile

from app.models import safe_name

LIVE_DIRNAME = "live"
STREAM = "system"
SOURCE_ID = "live"
SNIPPET_SUFFIX = ".flac"
TMP_SUFFIX = ".tmp"
WALL_FORMAT = "%Y%m%dT%H%M%S.%f"
GAP_MIN_SECONDS = 1.0
_SNIPPET = re.compile(r"^(\d{6})-(\d{8}T\d{6}\.\d{3})\.flac$")


def snippet_name(seq: int, wall: datetime) -> str:
    """'<seq 6 digits>-<YYYYMMDDTHHMMSS.mmm>.flac'. wall is the time of the first sample."""
    return f"{seq:06d}-{wall.strftime(WALL_FORMAT)[:-3]}{SNIPPET_SUFFIX}"


@dataclass
class Snippet:
    path: Path
    seq: int
    wall: datetime  # time of the first sample
    frames: int
    rate: int
    channels: int

    @property
    def seconds(self) -> float:
        return self.frames / self.rate


@dataclass
class Gap:
    at: float  # audio time where the gap is
    seconds: float  # wall-clock length of the missing audio


class Timeline:
    """Finished snippets in sequence order, placed end to end on one audio time axis."""

    def __init__(self, snippets: List[Snippet]):
        self.snippets = snippets
        self.starts: List[float] = []
        total = 0.0
        for snippet in snippets:
            self.starts.append(total)
            total += snippet.seconds
        self.total = total

    def gaps(self) -> List[Gap]:
        """Places where the wall clock jumps forward by GAP_MIN_SECONDS or more between snippets."""
        result = []
        for before, after, start in zip(self.snippets, self.snippets[1:], self.starts[1:]):
            expected = before.wall + timedelta(seconds=before.seconds)
            missing = (after.wall - expected).total_seconds()
            if missing >= GAP_MIN_SECONDS:
                result.append(Gap(start, missing))
        return result


class Session:
    """One live recording: <outdir>/live/<name>/."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._info: Dict[str, Snippet] = {}

    @classmethod
    def create(cls, outdir: Path, label: str, now: Optional[datetime] = None) -> "Session":
        """Make <outdir>/live/<YYYYMMDD-HHMMSS[-label]>/ and return it."""
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        clean_label = safe_name(label)
        name = f"{stamp}-{clean_label}" if clean_label else stamp
        session = cls(outdir / LIVE_DIRNAME / name)
        session.audio_dir.mkdir(parents=True, exist_ok=True)
        return session

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def audio_dir(self) -> Path:
        return self.folder / STREAM

    @property
    def partial(self) -> Path:
        return self.folder / "transcript.partial.jsonl"

    @property
    def transcript(self) -> Path:
        return self.folder / f"{self.name}.txt"

    @property
    def merged(self) -> Path:
        return self.folder / f"{self.name}{SNIPPET_SUFFIX}"

    @property
    def stopped_marker(self) -> Path:
        return self.folder / "stopped"

    def is_stopped(self) -> bool:
        return self.stopped_marker.exists()

    def mark_stopped(self) -> None:
        self.stopped_marker.touch()

    def clear_stopped(self) -> None:
        self.stopped_marker.unlink(missing_ok=True)

    def snippet_paths(self) -> List[Path]:
        """Finished snippet files, in sequence order. .tmp files are not included."""
        if not self.audio_dir.is_dir():
            return []
        return sorted(p for p in self.audio_dir.iterdir() if _SNIPPET.match(p.name))

    def tmp_paths(self) -> List[Path]:
        if not self.audio_dir.is_dir():
            return []
        return sorted(p for p in self.audio_dir.iterdir() if p.name.endswith(TMP_SUFFIX))

    def next_seq(self) -> int:
        paths = self.snippet_paths()
        return int(_SNIPPET.match(paths[-1].name).group(1)) + 1 if paths else 1

    def timeline(self) -> Timeline:
        """Read the header of each new finished snippet once, and return the timeline."""
        snippets = []
        for path in self.snippet_paths():
            snippet = self._info.get(path.name)
            if snippet is None:
                match = _SNIPPET.match(path.name)
                info = soundfile.info(str(path))
                snippet = Snippet(
                    path,
                    int(match.group(1)),
                    datetime.strptime(match.group(2), WALL_FORMAT),
                    info.frames,
                    info.samplerate,
                    info.channels,
                )
                self._info[path.name] = snippet
            snippets.append(snippet)
        return Timeline(snippets)


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
