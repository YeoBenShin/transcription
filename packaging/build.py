"""Build the desktop app with PyInstaller: dist/Transcription.app (zipped) on macOS, dist/Transcription/ (zipped) on Windows.

python packaging/build.py [--smoke]
--smoke: after the build, run the packaged worker on a short fixture with the tiny model to check that
the packaged app can transcribe (downloads the tiny model once into build/models).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_NAME = "Transcription"


def run(*command: str, **kwargs) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def build() -> Path:
    if sys.platform == "darwin":
        run("sh", str(ROOT / "live" / "native" / "build.sh"))
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST),
        "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "packaging" / "transcription.spec"))
    if sys.platform == "darwin":
        return DIST / f"{APP_NAME}.app"
    return DIST / APP_NAME


def worker(app: Path) -> Path:
    if sys.platform == "darwin":
        return app / "Contents" / "MacOS" / "transcription-worker"
    suffix = ".exe" if sys.platform == "win32" else ""
    return app / f"transcription-worker{suffix}"


def smoke(app: Path) -> None:
    """Import check of all engines, then a real transcription of the fixture with the tiny model."""
    sys.path.insert(0, str(ROOT))
    from desktop.models import catalog, download

    exe = str(worker(app))
    run(exe, "-m", "desktop.selfcheck")
    models = ROOT / "build" / "models"
    model = download.download(catalog.find("whisper-tiny"), models, lambda done, total: None)
    out = ROOT / "build" / "smoke-out"
    shutil.rmtree(out, ignore_errors=True)
    run(exe, "-m", "app", str(ROOT / "tests" / "fixtures" / "speech_22s.flac"), "--outdir", str(out),
        "--backend", "faster-whisper", "--model", str(model))
    text = next(out.glob("*.txt")).read_text(encoding="utf-8")
    print(text)
    if "meeting" not in text.lower():
        raise SystemExit("smoke test: the transcript does not have the expected words")


def archive(app: Path) -> Path:
    """Zip the app. On macOS ditto keeps the bundle's symlinks and signatures."""
    target = DIST / f"{APP_NAME}-{'macos' if sys.platform == 'darwin' else 'windows'}.zip"
    target.unlink(missing_ok=True)
    if sys.platform == "darwin":
        run("ditto", "-c", "-k", "--keepParent", str(app), str(target))
    else:
        shutil.make_archive(str(target.with_suffix("")), "zip", app.parent, app.name)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="test the packaged app on a fixture")
    args = parser.parse_args()
    app = build()
    if args.smoke:
        smoke(app)
    print(f"built {archive(app)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
