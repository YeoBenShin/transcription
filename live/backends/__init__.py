"""Speech-to-text backends, by name."""

from live.backends.base import SAMPLE_RATE, Backend, Segment, Word

__all__ = ["SAMPLE_RATE", "Backend", "Segment", "Word", "DEFAULT_BACKEND", "DEFAULT_MODEL", "names", "make"]

DEFAULT_BACKEND = "faster-whisper"
DEFAULT_MODEL = "base"


def names():
    return ["faster-whisper", "parakeet-mlx"]


def make(name: str, model: str) -> Backend:
    """Return the backend `name` with model `model`. Raise ValueError for an unknown name."""
    if name == "faster-whisper":
        from live.backends.faster_whisper import FasterWhisper

        return FasterWhisper(model)
    if name == "parakeet-mlx":
        from live.backends.parakeet_mlx import ParakeetMLX

        return ParakeetMLX(model)
    raise ValueError(f"unknown backend {name!r}. Known: {', '.join(names())}")
