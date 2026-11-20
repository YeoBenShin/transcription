"""User settings of the desktop app, saved as JSON in the data folder."""

import json
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from desktop import paths

SYSTEM_SOURCES = ("tap", "device", "loopback", "none")  # tap: macOS process tap; loopback: Windows WASAPI


@dataclass
class Settings:
    model_id: str = ""  # catalog id of the selected model
    output_dir: str = field(default_factory=lambda: str(paths.default_output_dir()))
    system_source: str = field(default_factory=lambda: Settings.default_system_source(sys.platform))
    system_device: str = ""  # input device (device) or playback device (loopback); "" is the default one
    mic_enabled: bool = False
    mic_device: str = ""  # "" is the default input device
    onboarded: bool = False

    @staticmethod
    def default_system_source(platform: str) -> str:
        if platform == "darwin":
            return "tap"
        if platform == "win32":
            return "loopback"
        return "none"

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """The saved settings, or the defaults if the file is missing or cannot be read. Unknown keys are ignored."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)


def settings_path() -> Path:
    return paths.data_dir() / "settings.json"
