"""Parakeet-mlx engine: token to word grouping, local model loading, app and live use. No model needed
except in the test marked `model` (opt in with -m model)."""

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from app.stages.common import parakeet


@dataclass
class Token:
    text: str
    start: float
    end: float


def test_tokens_join_into_words_at_leading_space():
    tokens = [Token(" Hel", 0.0, 0.2), Token("lo", 0.2, 0.4), Token(" world", 0.5, 0.9), Token(".", 0.9, 1.0)]
    words = parakeet.words_of(tokens)
    assert [(w.start, w.end, w.text) for w in words] == [(0.0, 0.4, "Hello"), (0.5, 1.0, "world.")]


def test_first_token_without_space_starts_a_word():
    words = parakeet.words_of([Token("Yes", 0.0, 0.3), Token(" no", 0.4, 0.6)])
    assert [w.text for w in words] == ["Yes", "no"]


def test_blank_tokens_are_left_out():
    words = parakeet.words_of([Token(" ", 0.0, 0.1), Token(" ok", 0.2, 0.3), Token("", 0.3, 0.3)])
    assert [(w.start, w.text) for w in words] == [(0.2, "ok")]


def test_lines_of_words_split_at_sentence_end():
    words = parakeet.words_of(
        [Token(" One", 0.0, 0.2), Token(" two.", 0.2, 0.5), Token(" Three", 0.6, 0.8), Token(" four", 0.8, 1.0)]
    )
    lines = parakeet.sentences(words)
    assert [(s[0].start, s[-1].end, " ".join(w.text for w in s)) for s in lines] == [
        (0.0, 0.5, "One two."), (0.6, 1.0, "Three four"),
    ]


def test_local_model_dir_needs_config_and_weights(tmp_path):
    with pytest.raises(FileNotFoundError):
        parakeet.check_model_dir(tmp_path)
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"")
    parakeet.check_model_dir(tmp_path)


class FakeParakeet:
    """Stands in for the loaded model: each call returns the next list of tokens, in seconds from the audio start."""

    def __init__(self, calls):
        self.calls = list(calls)
        self.lengths = []

    def tokens(self, samples):
        self.lengths.append(len(samples) / parakeet.SAMPLE_RATE)
        return self.calls.pop(0)


def test_live_backend_gives_one_segment_per_sentence(monkeypatch):
    from live.backends import make

    fake = FakeParakeet([[Token(" Hi", 0.0, 0.3), Token(" there.", 0.3, 0.6), Token(" Bye", 1.0, 1.2)]])
    monkeypatch.setattr(parakeet, "load", lambda model: fake)
    backend = make("parakeet-mlx", "some/dir")
    segments = backend.transcribe(np.zeros(parakeet.SAMPLE_RATE * 2, np.float32), prompt="ignored")
    assert [(s.start, s.end, s.text) for s in segments] == [(0.0, 0.6, "Hi there."), (1.0, 1.2, "Bye")]
    assert [w.text for w in segments[0].words] == ["Hi", "there."]


def test_file_transcription_resumes_at_last_written_sentence(monkeypatch, tmp_path):
    """A window of WINDOW_SECONDS: sentences that end before the window end minus the margin are written,
    the next window starts at the end of the last written sentence."""
    from app.stages.common import transcribe

    seconds = parakeet.WINDOW_SECONDS + 1
    monkeypatch.setattr(transcribe, "decode", lambda audio: np.zeros(int(seconds * parakeet.SAMPLE_RATE), np.float32))
    end = parakeet.WINDOW_SECONDS
    fake = FakeParakeet([
        [Token(" First.", 1.0, 2.0), Token(" Cut", end - 1.0, end)],  # "Cut" touches the window end: not written
        [Token(" Cut", 0.0, 1.0), Token(" again.", 1.0, 2.0)],
    ])
    monkeypatch.setattr(parakeet, "load", lambda model: fake)
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"x")
    lines = transcribe.transcribe(audio, "dir", tmp_path / "p.jsonl", "id", backend="parakeet-mlx")
    assert [(round(l.start, 3), l.text) for l in lines] == [(1.0, "First."), (2.0, "Cut again.")]
    assert fake.lengths == [parakeet.WINDOW_SECONDS, seconds - 2.0]  # the last window ends at the audio end


@pytest.mark.model
@pytest.mark.skipif(sys.platform != "darwin", reason="mlx needs Apple Silicon")
def test_real_model_transcribes_fixture():
    """Needs PARAKEET_MODEL_DIR with a downloaded model."""
    import os

    from app.stages.common.transcribe import decode

    model_dir = os.environ.get("PARAKEET_MODEL_DIR")
    if not model_dir:
        pytest.skip("set PARAKEET_MODEL_DIR")
    samples = decode(Path(__file__).parent / "fixtures" / "speech_22s.flac")
    words = parakeet.words_of(parakeet.load(model_dir).tokens(samples))
    assert len(words) > 20
