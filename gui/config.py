"""Persisted settings for the ED Colony Scout GUI.

Stored under %APPDATA%\\EDColonyScout\\config.json (Windows), deliberately
independent of where the app's own executable/script lives, since a frozen
exe on the Desktop still needs to know where output reports (and the
extracted sector library) should go.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "EDColonyScout"

# Displayed in the window title.
APP_VERSION = "1.1.0"


def _default_out_dir() -> str:
    """Per-user writable output dir, independent of wherever the app itself
    is installed -- there's no git checkout to anchor to for an installed app."""
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return str(base / APP_NAME / "out")


def default_sector_library_dir() -> str:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return str(base / APP_NAME / "sector_library")


DEFAULT_CONFIG: dict[str, Any] = {
    "db_paths": [],
    "sector_label": "",
    "origin_system": "",
    "max_jump_ly": 15.0,
    "max_hops": 8,
    "dense_top_n": 20,
    "out_dir": _default_out_dir(),
    "scoring_rubric": {},  # empty = stock rubric (see gui/rubric.rubric_from_dict)
    "galaxy_dump_path": "",
    "sector_prefixes": [],
    "sector_library_dir": default_sector_library_dir(),
    # -- Path Finder tab --
    "pf_origin_system": "",
    "pf_target_system": "",
    "pf_max_jump_ly": 15.0,
    "pf_any_hops": True,  # separate toggle; pf_max_hops keeps its last value even while this is True
    "pf_max_hops": 20,
    "pf_candidate_count": 10,
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
                # v0.1.0 used a single "db_path" string; migrate forward
                # into "db_paths" the first time an old config is loaded.
                if not config.get("db_paths") and saved.get("db_path"):
                    config["db_paths"] = [saved["db_path"]]
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
