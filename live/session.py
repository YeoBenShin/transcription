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
LIVE_SUFFIX = ".pcm"
LIVE_DTYPE = "<f4"  # raw float32 little-endian, channels interleaved, no header
TMP_SUFFIX = ".tmp"
WALL_FORMAT = "%Y%m%dT%H%M%S.%f"
GAP_MIN_SECONDS = 1.0
_SNIPPET = re.compile(r"^(\d{6})-(\d{8}T\d{6}\.\d{3})\.flac$")
_LIVE = re.compile(r"^(\d{6})-(\d{8}T\d{6}\.\d{3})-(\d+)x(\d+)\.pcm$")


def snippet_name(seq: int, wall: datetime) -> str:
    """'<seq 6 digits>-<YYYYMMDDTHHMMSS.mmm>.flac'. wall is the time of the first sample."""
    return f"{seq:06d}-{wall.strftime(WALL_FORMAT)[:-3]}{SNIPPET_SUFFIX}"


def live_name(seq: int, wall: datetime, rate: int, channels: int) -> str:
    """'<seq>-<wall>-<rate>x<channels>.pcm': the snippet that the recorder still writes."""
    return f"{seq:06d}-{wall.strftime(WALL_FORMAT)[:-3]}-{rate}x{channels}{LIVE_SUFFIX}"


def sealed_path(live: Path) -> Path:
    """The FLAC snippet path that replaces the live file `live`."""
    match = _LIVE.match(live.name)
    return live.with_name(f"{match.group(1)}-{match.group(2)}{SNIPPET_SUFFIX}")


def live_snippet(path: Path) -> "Snippet":
    """The live file at `path` as a Snippet, with the complete frames it holds now (0 if it is gone)."""
    match = _LIVE.match(path.name)
    rate, channels = int(match.group(3)), int(match.group(4))
    try:
        frames = path.stat().st_size // (4 * channels)
    except FileNotFoundError:
        frames = 0
    wall = datetime.strptime(match.group(2), WALL_FORMAT)
    return Snippet(path, int(match.group(1)), wall, frames, rate, channels, live=True)


@dataclass
class Snippet:
    path: Path
    seq: int
    wall: datetime  # time of the first sample
    frames: int
    rate: int
    channels: int
    live: bool = False  # raw .pcm file that the recorder still appends to

    @property
    def seconds(self) -> float:
        return self.frames / self.rate


@dataclass
class Gap:
    at: float  # audio time where the gap is
    seconds: float  # wall-clock length of the missing audio


class Timeline:
    """Snippets in sequence order, placed end to end on one audio time axis.

    The last one can be the live snippet, with the frames it holds at the time of the listing.
    """

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

    def live_paths(self) -> List[Path]:
        """Live .pcm files, in sequence order."""
        if not self.audio_dir.is_dir():
            return []
        return sorted(p for p in self.audio_dir.iterdir() if _LIVE.match(p.name))

    def next_seq(self) -> int:
        seqs = [int(p.name[:6]) for p in self.snippet_paths() + self.live_paths()]
        return max(seqs) + 1 if seqs else 1

    def live_snippet(self, seq: int) -> Optional[Snippet]:
        """The live snippet with sequence `seq` and at least 1 frame, or None."""
        for path in self.live_paths():
            snippet = live_snippet(path)
            if snippet.seq == seq and snippet.frames:
                return snippet
        return None

    def timeline(self) -> Timeline:
        """Read the header of each new finished snippet once, add the live snippet, and return the timeline.

        The live snippet is used only if its sequence is the next after the last finished
        snippet. Thus a listing taken while the recorder replaces a live file by its FLAC never
        puts a later snippet in the place of a missing one.
        """
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
        live = self.live_snippet(snippets[-1].seq + 1 if snippets else 1)
        if live is not None:
            snippets.append(live)
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
