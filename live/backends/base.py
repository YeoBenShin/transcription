"""The interface that every speech-to-text backend has."""

from dataclasses import dataclass, field
from typing import List, Optional, Protocol

import numpy as np

SAMPLE_RATE = 16000  # backends get 16 kHz mono float32 audio


@dataclass
class Word:
    start: float  # seconds from the start of the audio given
    end: float
    text: str  # no leading or trailing space


@dataclass
class Segment:
    start: float  # seconds from the start of the audio given
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


class Backend(Protocol):
    name: str
    model: str

    def transcribe(self, audio: np.ndarray, prompt: Optional[str]) -> List[Segment]:
        """Transcribe 16 kHz mono float32 audio. `prompt` is earlier text for context.

        Each segment has its words with their own times.
        """
