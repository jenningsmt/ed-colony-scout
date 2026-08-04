"""Persisted settings for the ED Colony Scout GUI.

Stored under %APPDATA%\\EDColonyScout\\config.json (Windows), deliberately
independent of where the app's own executable/script lives, since a frozen
exe on the Desktop still needs to know where output reports should go.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "EDColonyScout"

# Displayed in the window title.
APP_VERSION = "0.1.0"


def _default_out_dir() -> str:
    """Per-user writable output dir, independent of wherever the app itself
    is installed -- there's no git checkout to anchor to for an installed app."""
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return str(base / APP_NAME / "out")


DEFAULT_CONFIG: dict[str, Any] = {
    "db_path": "",
    "sector_label": "",
    "origin_system": "",
    "max_jump_ly": 15.0,
    "max_hops": 8,
    "dense_top_n": 20,
    "out_dir": _default_out_dir(),
}


def config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / APP_NAME / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
