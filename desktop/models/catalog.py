"""The speech models the app offers for download, and where an installed model is on disk."""

import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

PARTIAL_SUFFIX = ".partial"


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int  # bytes


@dataclass(frozen=True)
class ModelSpec:
    id: str  # folder name in the models folder
    title: str
    backend: str  # "faster-whisper" or "parakeet-mlx"
    repo: str  # Hugging Face repo
    revision: str  # commit of the repo, so each download gets the same files
    files: Tuple[ModelFile, ...]
    note: str

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def apple_silicon_only(self) -> bool:
        return self.backend == "parakeet-mlx"


VOCAB_TXT = ModelFile("vocabulary.txt", 459861)
VOCAB_JSON = ModelFile("vocabulary.json", 1068114)
PREPROCESSOR = ModelFile("preprocessor_config.json", 340)


def whisper(size: str, title: str, repo: str, revision: str, sizes: Tuple[int, int, int], extra: Tuple[ModelFile, ...],
            note: str) -> ModelSpec:
    """A faster-whisper (CTranslate2) model. sizes: config.json, model.bin, tokenizer.json."""
    config, weights, tokenizer = sizes
    files = (ModelFile("config.json", config), ModelFile("model.bin", weights), ModelFile("tokenizer.json", tokenizer))
    return ModelSpec(f"whisper-{size}", title, "faster-whisper", repo, revision, files + extra, note)


def parakeet(version: str, title: str, revision: str, sizes: Tuple[int, int], note: str) -> ModelSpec:
    """A parakeet-mlx model. sizes: config.json, model.safetensors."""
    config, weights = sizes
    files = (ModelFile("config.json", config), ModelFile("model.safetensors", weights))
    name = f"parakeet-tdt-0.6b-{version}"
    return ModelSpec(name, title, "parakeet-mlx", f"mlx-community/{name}", revision, files, note)


CATALOG: List[ModelSpec] = [
    whisper("tiny", "Whisper Tiny", "Systran/faster-whisper-tiny", "d90ca5fe260221311c53c58e660288d3deb8d356",
            (2249, 75538270, 2203239), (VOCAB_TXT,), "Fastest, lowest accuracy"),
    whisper("base", "Whisper Base", "Systran/faster-whisper-base", "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
            (2309, 145217532, 2203239), (VOCAB_TXT,), "Fast. Good for live captions on most computers"),
    whisper("small", "Whisper Small", "Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120",
            (2370, 483546902, 2203239), (VOCAB_TXT,), "Balanced speed and accuracy"),
    whisper("medium", "Whisper Medium", "Systran/faster-whisper-medium", "08e178d48790749d25932bbc082711ddcfdfbc4f",
            (2257, 1527906378, 2203239), (VOCAB_TXT,), "Accurate. Slow on CPU"),
    whisper("large-v3", "Whisper Large v3", "Systran/faster-whisper-large-v3",
            "edaa852ec7e145841d8ffdb056a99866b5f0a478", (2394, 3087284237, 2480617), (VOCAB_JSON, PREPROCESSOR),
            "Most accurate Whisper. Very slow on CPU"),
    whisper("large-v3-turbo", "Whisper Large v3 Turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf", (2263, 1617884929, 2710337), (VOCAB_JSON, PREPROCESSOR),
            "Almost the accuracy of Large v3, faster"),
    parakeet("v2", "Parakeet v2 (English)", "8ae155301e23d820d82aa60d24817c900e69e487", (36176, 2471559904),
             "Apple Silicon GPU. Very fast and accurate. English only"),
    parakeet("v3", "Parakeet v3 (25 languages)", "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15", (244093, 2508288736),
             "Apple Silicon GPU. Very fast. European languages"),
]


def is_apple_silicon(system: str, machine: str) -> bool:
    return system == "darwin" and machine == "arm64"


def available(system: Optional[str] = None, machine: Optional[str] = None) -> List[ModelSpec]:
    """The models this computer can run. Parakeet-mlx needs Apple Silicon."""
    system = system or sys.platform
    machine = machine or platform.machine()
    return [m for m in CATALOG if not m.apple_silicon_only or is_apple_silicon(system, machine)]


def find(model_id: str) -> Optional[ModelSpec]:
    return next((m for m in CATALOG if m.id == model_id), None)


def model_path(model: ModelSpec, root: Path) -> Path:
    return Path(root) / model.id


def partial_path(model: ModelSpec, root: Path) -> Path:
    return Path(root) / (model.id + PARTIAL_SUFFIX)


def is_installed(model: ModelSpec, root: Path) -> bool:
    """A download is renamed to its folder only after all files are complete."""
    return model_path(model, root).is_dir()


def delete(model: ModelSpec, root: Path) -> None:
    """Remove the installed model and any partial download of it."""
    for path in (model_path(model, root), partial_path(model, root)):
        shutil.rmtree(path, ignore_errors=True)
