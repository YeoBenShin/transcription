"""Source: audio or video file on disk. Transcribe with Whisper."""

from pathlib import Path
from typing import Optional

from app.models import Options, Transcript, folder_name
from app.stages.common.retry import retry
from app.stages.common.transcribe import transcribe


class LocalFileSource:
    name = "local file"

    def matches(self, raw: str) -> bool:
        return Path(raw).expanduser().is_file()

    def get_transcript(self, raw: str, options: Options) -> Optional[Transcript]:
        path = Path(raw).expanduser()
        workdir = options.outdir / folder_name(None, path.stem)
        lines = retry(lambda: transcribe(path, options.model, workdir, path.stem))
        if not lines:
            return None
        return Transcript(path.stem, lines, origin="faster-whisper")
