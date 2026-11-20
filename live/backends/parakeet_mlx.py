"""Backend: parakeet-mlx on Apple Silicon, with the model cache of app.stages.common.parakeet."""

from typing import List, Optional

import numpy as np

from app.models import clean
from app.stages.common import parakeet
from live.backends.base import Segment, Word


class ParakeetMLX:
    name = "parakeet-mlx"

    def __init__(self, model: str):
        self.model = model

    def transcribe(self, audio: np.ndarray, prompt: Optional[str]) -> List[Segment]:
        """One segment per sentence. Parakeet takes no prompt, so `prompt` is not used."""
        words = parakeet.words_of(parakeet.load(self.model).tokens(audio))
        return [
            Segment(
                sentence[0].start,
                sentence[-1].end,
                clean(" ".join(w.text for w in sentence)),
                [Word(w.start, w.end, w.text) for w in sentence],
            )
            for sentence in parakeet.sentences(words)
        ]
