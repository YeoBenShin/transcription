"""End of a session: finish the transcript, merge the run files, remove the work files."""

from typing import Optional

from live import backends, console, transcriber
from live.audio import merge, mix
from live.progress import read_header
from live.session import STREAMS, AlreadyRunning, Session, claim, recorder_role, release, running_pid


def cleanup(session: Session) -> None:
    """Remove the progress files, run files, stop marker and the empty stream folders."""
    for stream in session.streams():
        stream.partial.unlink(missing_ok=True)
        for path in stream.run_paths():
            path.unlink()
        if stream.audio_dir.is_dir() and not any(stream.audio_dir.iterdir()):
            stream.audio_dir.rmdir()
    session.clear_stopped()


def saved_header(session: Session) -> dict:
    """The progress file header of the first stream that has one, or {}."""
    for stream in session.streams():
        header = read_header(stream.partial)
        if header:
            return header
    return {}


def finalize(session: Session, backend_name: Optional[str] = None, model: Optional[str] = None) -> None:
    """Mark the session stopped, transcribe the audio that is left, write the FLAC files, then clean up.

    One stream with audio: <name>.flac. More than one: <name>.<stream>.flac per stream, then
    <name>.flac with all streams mixed. <name>.flac is written last.
    The backend and model default to the ones in the progress file header.
    If <name>.flac exists, the merge was done and checked before, so only the clean-up runs.
    Raise AlreadyRunning if a recorder or transcriber still runs for the session.
    """
    for name in STREAMS:
        pid = running_pid(session, recorder_role(name))
        if pid is not None:
            raise AlreadyRunning(f"a {name} recorder (pid {pid}) still runs for {session.folder}")
    if session.merged.exists():
        cleanup(session)
        return
    session.mark_stopped()

    saved = saved_header(session)
    backend = backends.make(
        backend_name or saved.get("backend", backends.DEFAULT_BACKEND),
        model or saved.get("model", backends.DEFAULT_MODEL),
    )
    claim(session, "transcriber")
    try:
        transcriber.run(session, backend)
    finally:
        release(session, "transcriber")

    timelines = [(stream, stream.timeline()) for stream in session.streams()]
    timelines = [(stream, timeline) for stream, timeline in timelines if timeline.files]
    if len(timelines) == 1:
        files = timelines[0][1].files
        frames = merge(files, session.merged)
        console.say(f"merged {len(files)} run file(s): {frames / files[0].rate:.1f} s")
    elif timelines:
        for stream, timeline in timelines:
            frames = merge(timeline.files, stream.merged)
            console.say(f"merged {stream.name}: {len(timeline.files)} run file(s), {frames / timeline.files[0].rate:.1f} s")
        reference = timelines[0][1].files
        frames = mix(reference, [timeline.files for _, timeline in timelines[1:]], session.merged)
        console.say(f"mixed {len(timelines)} streams: {frames / reference[0].rate:.1f} s")
    cleanup(session)
