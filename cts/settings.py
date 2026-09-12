from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


APP_DIR_NAME = "HMDSCharacterTranslationStudio"
SETTINGS_VERSION = 3


def _config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / APP_DIR_NAME


@dataclass
class AppSettings:
    language: str = "pt-BR"
    preview_scale: float = 1.45
    thumbnail_size: int = 24
    preview_shadow: bool = True
    preview_shadow_color: str = "#777777"
    preview_shadow_x: int = -1
    preview_shadow_y: int = 0
    scroll_speed: int = 4
    layout_preset: str = "balanced"  # compact | balanced | preview
    highlight_palette: str = "classic"  # classic | blue | warm
    show_progress: bool = True
    show_thumbnails: bool = True

    @classmethod
    def load(cls) -> "AppSettings":
        path = settings_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out = cls()
            for field in asdict(out):
                if field in data:
                    setattr(out, field, data[field])

            if out.language not in {"pt-BR", "en", "es"}:
                out.language = "pt-BR"
            out.preview_scale = max(0.75, min(2.25, float(out.preview_scale)))
            out.thumbnail_size = max(16, min(40, int(out.thumbnail_size)))
            out.preview_shadow_x = max(-4, min(4, int(out.preview_shadow_x)))
            out.preview_shadow_y = max(-4, min(4, int(out.preview_shadow_y)))
            out.scroll_speed = max(1, min(12, int(out.scroll_speed)))
            if not isinstance(out.preview_shadow_color, str) or not out.preview_shadow_color.startswith("#") or len(out.preview_shadow_color) != 7:
                out.preview_shadow_color = "#777777"
            if out.layout_preset not in {"compact", "balanced", "preview"}:
                out.layout_preset = "balanced"
            if out.highlight_palette not in {"classic", "blue", "warm"}:
                out.highlight_palette = "classic"

            version = int(data.get("_version", 0) or 0)
            if version < 3:
                # Import the old single positive offset as the requested 1px-left shadow.
                old_off = max(0, min(4, int(data.get("preview_shadow_offset", 1) or 1)))
                out.preview_shadow_x = -old_off if old_off else 0
                out.preview_shadow_y = 0
                out.preview_shadow_color = "#777777"
            return out
        except Exception:
            return cls()

    def save(self) -> Path:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["_version"] = SETTINGS_VERSION
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def settings_path() -> Path:
    return _config_dir() / "settings.json"
