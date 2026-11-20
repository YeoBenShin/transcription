"""Backend: faster-whisper on CPU, with the model cache of app.stages.common.transcribe."""

from typing import List, Optional

import numpy as np

from app.models import TARGET_LANGUAGE, clean
from app.stages.common.transcribe import load_model
from live.backends.base import SAMPLE_RATE, Segment, Word

TOKENS_PER_SECOND = 10  # most tokens one second of audio can give
TOKEN_MARGIN = 20
MAX_LENGTH = 448  # most tokens Whisper takes in one window: prompt plus output
MAX_PROMPT = 227  # longest prompt faster-whisper makes: 223 text tokens plus 4 control tokens
MAX_NEW_TOKENS = MAX_LENGTH - MAX_PROMPT


class FasterWhisper:
    name = "faster-whisper"

    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio: np.ndarray, prompt: Optional[str]) -> List[Segment]:
        segments, _ = load_model(self.model).transcribe(
            audio,
            language=TARGET_LANGUAGE,
            vad_filter=True,
            initial_prompt=prompt,
            word_timestamps=True,
            temperature=0.0,
            max_new_tokens=min(int(len(audio) / SAMPLE_RATE * TOKENS_PER_SECOND) + TOKEN_MARGIN, MAX_NEW_TOKENS),
        )
        return [
            Segment(s.start, s.end, clean(s.text), [Word(w.start, w.end, clean(w.word)) for w in s.words or []])
            for s in segments
        ]
