"""End of a session: finish the transcript, merge the snippets, remove the work files."""

from typing import Optional

from live import backends, transcriber
from live.audio import merge
from live.progress import read_header
from live.session import AlreadyRunning, Session, claim, release, running_pid


def cleanup(session: Session) -> None:
    """Remove the progress file, snippets, .tmp files, stop marker and the empty audio folder."""
    session.partial.unlink(missing_ok=True)
    for path in session.snippet_paths() + session.tmp_paths():
        path.unlink()
    session.clear_stopped()
    if session.audio_dir.is_dir() and not any(session.audio_dir.iterdir()):
        session.audio_dir.rmdir()


def finalize(session: Session, backend_name: Optional[str] = None, model: Optional[str] = None) -> None:
    """Mark the session stopped, transcribe the audio that is left, write <name>.flac, then clean up.

    The backend and model default to the ones in the progress file header.
    If <name>.flac exists, the merge was done and checked before, so only the clean-up runs.
    Raise AlreadyRunning if a recorder or transcriber still runs for the session.
    """
    pid = running_pid(session, "recorder")
    if pid is not None:
        raise AlreadyRunning(f"a recorder (pid {pid}) still runs for {session.folder}")
    if session.merged.exists():
        cleanup(session)
        return
    session.mark_stopped()

    saved = read_header(session.partial) or {}
    backend = backends.make(
        backend_name or saved.get("backend", backends.DEFAULT_BACKEND),
        model or saved.get("model", backends.DEFAULT_MODEL),
    )
    claim(session, "transcriber")
    try:
        transcriber.run(session, backend)
    finally:
        release(session, "transcriber")

    timeline = session.timeline()
    if timeline.snippets:
        frames = merge(timeline.snippets, session.merged)
        print(f"merged {len(timeline.snippets)} snippets: {frames / timeline.snippets[0].rate:.1f} s")
    cleanup(session)
