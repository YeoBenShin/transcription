"""Parakeet-mlx engine (Apple Silicon): load a local model, turn 16 kHz audio into timed tokens, words and sentences."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Protocol, Sequence

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SECONDS = 120.0  # longest audio given to the model in one call by the file transcription
EDGE_SECONDS = 5.0  # a sentence that ends this close to the end of a window that is not the last waits for the next
SENTENCE_END = (".", "?", "!")
CONFIG_FILE = "config.json"
WEIGHTS_FILE = "model.safetensors"


class TimedToken(Protocol):
    text: str
    start: float
    end: float


@dataclass
class TimedWord:
    start: float
    end: float
    text: str


def words_of(tokens: Sequence[TimedToken]) -> List[TimedWord]:
    """Join subword tokens into words. A token whose text starts with a space starts a new word.
    Tokens with no text other than spaces are left out."""
    words: List[TimedWord] = []
    for token in tokens:
        text = token.text
        if not text.strip():
            continue
        if text.startswith(" ") or not words:
            words.append(TimedWord(token.start, token.end, text.strip()))
        else:
            words[-1].text += text.strip()
            words[-1].end = token.end
    return words


def sentences(words: List[TimedWord]) -> List[List[TimedWord]]:
    """Split words into sentences after each word that ends with . ? or !"""
    result: List[List[TimedWord]] = []
    current: List[TimedWord] = []
    for word in words:
        current.append(word)
        if word.text.endswith(SENTENCE_END):
            result.append(current)
            current = []
    if current:
        result.append(current)
    return result


def check_model_dir(model_dir: Path) -> None:
    """Raise FileNotFoundError if model_dir does not have the config and weights files."""
    for name in (CONFIG_FILE, WEIGHTS_FILE):
        if not (Path(model_dir) / name).is_file():
            raise FileNotFoundError(f"{model_dir} has no {name}: it is not a parakeet-mlx model folder")


class LoadedParakeet:
    """A parakeet-mlx model in memory."""

    def __init__(self, model):
        self.model = model

    def tokens(self, samples: np.ndarray) -> list:
        """Timed tokens of 16 kHz mono float32 audio, in seconds from its start."""
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        if len(samples) < self.model.preprocessor_config.hop_length:
            return []
        mel = get_logmel(mx.array(samples), self.model.preprocessor_config)
        return self.model.generate(mel)[0].tokens


@lru_cache(maxsize=1)
def load(model: str) -> LoadedParakeet:
    """Load a parakeet-mlx model once per process. `model` is a local model folder, or a Hugging Face
    repo id that is downloaded into the Hugging Face cache."""
    import mlx.core as mx
    from mlx.utils import tree_flatten, tree_unflatten
    from parakeet_mlx.utils import from_config

    folder = Path(model).expanduser()
    if not folder.is_dir():
        from huggingface_hub import snapshot_download

        folder = Path(snapshot_download(model, allow_patterns=[CONFIG_FILE, WEIGHTS_FILE]))
    check_model_dir(folder)
    loaded = from_config(json.loads((folder / CONFIG_FILE).read_text(encoding="utf-8")))
    loaded.load_weights(str(folder / WEIGHTS_FILE))
    weights = [(k, v.astype(mx.bfloat16)) for k, v in tree_flatten(loaded.parameters())]
    loaded.update(tree_unflatten(weights))
    return LoadedParakeet(loaded)
