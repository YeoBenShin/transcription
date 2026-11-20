"""The command lines of the child processes the app runs: live recording, file, folder and URL
transcription, merge and finalize. Each is `<executable> -u -m <module> ...`."""

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from desktop.models.catalog import ModelSpec, find, model_path
from desktop.settings import Settings

SOURCE_ROOT = Path(__file__).resolve().parents[1]
WORKER_NAME = "transcription-worker"  # console executable next to the app executable in the packaged app


def executable() -> str:
    """Python from source. In the packaged app, the console worker executable next to the app."""
    if getattr(sys, "frozen", False):
        suffix = ".exe" if sys.platform == "win32" else ""
        return str(Path(sys.executable).with_name(WORKER_NAME + suffix))
    return sys.executable


def module(name: str, *args: str) -> List[str]:
    return [executable(), "-u", "-m", name, *args]


def engine_args(model: ModelSpec, models_dir: Path) -> List[str]:
    return ["--backend", model.backend, "--model", str(model_path(model, models_dir))]


def selected_model(settings: Settings) -> ModelSpec:
    model = find(settings.model_id)
    if model is None:
        raise ValueError("no model is selected. Download and select one in Settings > Model")
    return model


def live_sources(settings: Settings) -> List[str]:
    """`live start` source options from the settings. Raise ValueError if nothing is to be recorded."""
    mic = ["--mic", settings.mic_device] if settings.mic_device else ["--mic"]
    if settings.system_source == "none":
        if not settings.mic_enabled:
            raise ValueError("nothing to record: turn on system audio or the microphone in Settings > Recording")
        return ["--mic-only"] + (["--mic", settings.mic_device] if settings.mic_device else [])
    if settings.system_source == "tap":
        system = ["--tap"]
    elif settings.system_source == "loopback":
        system = ["--loopback"] + (["--device", settings.system_device] if settings.system_device else [])
    else:
        system = ["--device", settings.system_device or "BlackHole"]
    return system + (mic if settings.mic_enabled else [])


def live_start(settings: Settings, models_dir: Path) -> List[str]:
    return module("live", "start", "--stdin-stop", "--outdir", settings.output_dir,
                  *engine_args(selected_model(settings), models_dir), *live_sources(settings))


def transcribe_file(settings: Settings, models_dir: Path, path: str) -> List[str]:
    return module("app", path, "--outdir", settings.output_dir, *engine_args(selected_model(settings), models_dir))


transcribe_url = transcribe_file


def transcribe_folder(settings: Settings, models_dir: Path, folder: str) -> List[str]:
    return module("app.batch", "--inbox", folder, "--keep", "--outdir", settings.output_dir,
                  *engine_args(selected_model(settings), models_dir))


def merge(sources: Sequence[Tuple[str, str]], name: str, outdir: str) -> List[str]:
    """sources: (label, path) pairs; an empty label gives lines with no label."""
    files = [f"{label}={path}" if label else path for label, path in sources]
    return module("transcript_merge", *files, "--name", name, "--outdir", outdir)


def finalize(settings: Settings, models_dir: Path, session: str) -> List[str]:
    model = find(settings.model_id)
    engine = engine_args(model, models_dir) if model else []
    return module("live", "finalize", session, *engine)


def child_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment of a child: JSON events from live, unbuffered UTF-8 output, and the source root on
    the import path when the app runs from source."""
    env = dict(os.environ if base is None else base)
    env.update({"LIVE_EVENTS": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(SOURCE_ROOT), env.get("PYTHONPATH", "")) if p)
    return env
