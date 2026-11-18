"""Backend: faster-whisper on CPU, with the model cache of app.stages.common.transcribe."""

from typing import List, Optional

import numpy as np

from app.models import TARGET_LANGUAGE, clean
from app.stages.common.transcribe import load_model
from live.backends.base import Segment


class FasterWhisper:
    name = "faster-whisper"

    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio: np.ndarray, prompt: Optional[str]) -> List[Segment]:
        segments, _ = load_model(self.model).transcribe(
            audio, language=TARGET_LANGUAGE, vad_filter=True, initial_prompt=prompt
        )
        return [Segment(s.start, s.end, clean(s.text)) for s in segments]
