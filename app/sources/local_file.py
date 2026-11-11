"""Source: audio or video file on disk. Transcribe with Whisper."""

from pathlib import Path
from typing import Optional

from app.models import Options, Transcript, output_name
from app.stages.common.retry import retry
from app.stages.common.transcribe import partial_path, transcribe
from app.stages.local.file_info import fetch_file_info


class LocalFileSource:
    name = "local file"

    def matches(self, raw: str) -> bool:
        return Path(raw).expanduser().is_file()

    def get_transcript(self, raw: str, options: Options) -> Optional[Transcript]:
        path = Path(raw).expanduser()
        info = fetch_file_info(path)
        name = output_name(info.recorded, info.author, path.stem, path.stem)
        partial = partial_path(options.temp_dir, name)
        lines = retry(lambda: transcribe(path, options.model, partial, path.stem))
        if not lines:
            return None
        return Transcript(path.stem, lines, origin="faster-whisper", name=name)
