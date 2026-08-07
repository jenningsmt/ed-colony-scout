"""Read-only sqlite helpers backing the Run tab's DB/origin-system picker."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gui import config as config_module

# Matches config.default_sector_library_dir() (the %LOCALAPPDATA%-based
# path a fresh config actually resolves to) rather than a path relative to
# this source file -- the latter resolves somewhere inside a PyInstaller
# temp/install bundle when frozen, where "data/sector_library" won't exist.
# Only used as a Tkinter file-dialog initialdir cosmetic fallback (see
# gui/app.py's Add... button), never for real data resolution.
DEFAULT_SECTOR_LIBRARY_DIR = Path(config_module.default_sector_library_dir())


def _derive_sector_label_one(db_path: str) -> str:
    """Best-effort sector label from a sector DB filename, e.g.
    'sector_Parrots_Head_Sector.sqlite' -> 'Parrots Head Sector'."""
    stem = Path(db_path).stem
    if stem.startswith("sector_"):
        stem = stem[len("sector_"):]
    return stem.replace("_", " ").strip()


def derive_sector_label(db_paths: list[str]) -> str:
    """Joins each DB's derived label, deduped, e.g.
    ['sector_Parrots_Head_Sector.sqlite', 'sector_eafots.sqlite'] ->
    'Parrots Head Sector + eafots'. Only used to pre-fill the Sector Label
    field -- exact spelling doesn't matter, since it only feeds
    sanitize_prefix() for output filenames."""
    labels = []
    for p in db_paths:
        label = _derive_sector_label_one(p)
        if label and label not in labels:
            labels.append(label)
    return " + ".join(labels)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_system_names(db_paths: list[str], query: str, limit: int = 50) -> list[str]:
    """Case-insensitive substring search over systems.name across every
    valid db_path, merged/deduped/sorted. Returns [] for an empty query or
    no valid DBs -- never used to dump a full table into a dropdown."""
    query = query.strip()
    valid = [p for p in db_paths if p and Path(p).exists()]
    if not query or not valid:
        return []
    like = f"%{_escape_like(query)}%"
    names: set[str] = set()
    for db_path in valid:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM systems WHERE LOWER(name) LIKE LOWER(?) ESCAPE '\\' "
                "ORDER BY name LIMIT ?",
                (like, limit),
            ).fetchall()
        finally:
            conn.close()
        names.update(r[0] for r in rows)
    return sorted(names)[:limit]


def system_exists(db_paths: list[str], name: str) -> bool:
    if not name:
        return False
    for db_path in db_paths:
        if not db_path or not Path(db_path).exists():
            continue
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT 1 FROM systems WHERE name = ? LIMIT 1", (name,)).fetchone()
        finally:
            conn.close()
        if row is not None:
            return True
    return False


@dataclass
class BodyDetail:
    body_name: str
    body_type: Optional[str]
    sub_type: Optional[str]
    terraform_state: Optional[str]
    is_landable: Optional[bool]
    signal_counts: dict[str, int] = field(default_factory=dict)
    genuses: list[str] = field(default_factory=list)


@dataclass
class RingDetail:
    body_name: str
    ring_name: str
    ring_class: Optional[str]
    mass_mt: Optional[float]
    inner_rad: Optional[float]
    outer_rad: Optional[float]


@dataclass
class SystemDetail:
    system_name: str
    x: Optional[float]
    y: Optional[float]
    z: Optional[float]
    bodies: list[BodyDetail] = field(default_factory=list)
    rings: list[RingDetail] = field(default_factory=list)


def _parse_body_detail(body_name: str, body_type: Optional[str], sub_type: Optional[str],
                        terraform_state: Optional[str], raw_json: Optional[str]) -> BodyDetail:
    """Pulls isLandable and biosignal/genus data out of a body's raw_json.
    Nothing in the codebase parses these fields yet, so a malformed or
    missing blob just yields the "nothing known" defaults rather than
    failing the whole system lookup."""
    is_landable: Optional[bool] = None
    signal_counts: dict[str, int] = {}
    genuses: list[str] = []
    try:
        data = json.loads(raw_json) if raw_json else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        data = {}
    is_landable = data.get("isLandable")
    signals_block = data.get("signals") or {}
    # Spansh nests the actual per-type counts one level deeper, under a
    # "signals" key inside the body's own "signals" block.
    signal_counts = dict(signals_block.get("signals") or {})
    genuses = list(signals_block.get("genuses") or [])
    return BodyDetail(
        body_name=body_name,
        body_type=body_type,
        sub_type=sub_type,
        terraform_state=terraform_state,
        is_landable=is_landable,
        signal_counts=signal_counts,
        genuses=genuses,
    )


def _parse_ring_detail(body_name: str, ring_name: str, raw_json: Optional[str]) -> RingDetail:
    """rings.ring_class/mass_mt/inner_rad/outer_rad are unreliable (a
    pre-existing extraction key-casing mismatch leaves them NULL for every
    ring in every sector DB observed so far -- the extractor looks for
    ringClass/massMT/innerRad, but Spansh's actual keys are type/mass/
    innerRadius/outerRadius). scripts/colonization_reachability_export.py's
    extract_dense_system_details() already works around this by reading
    rings.raw_json directly; this does the same."""
    ring_class: Optional[str] = None
    mass_mt: Optional[float] = None
    inner_rad: Optional[float] = None
    outer_rad: Optional[float] = None
    try:
        data = json.loads(raw_json) if raw_json else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        data = {}
    ring_class = data.get("type") or None
    try:
        mass_mt = float(data["mass"]) if data.get("mass") is not None else None
    except (TypeError, ValueError):
        mass_mt = None
    try:
        inner_rad = float(data["innerRadius"]) if data.get("innerRadius") is not None else None
    except (TypeError, ValueError):
        inner_rad = None
    try:
        outer_rad = float(data["outerRadius"]) if data.get("outerRadius") is not None else None
    except (TypeError, ValueError):
        outer_rad = None
    return RingDetail(
        body_name=body_name,
        ring_name=ring_name,
        ring_class=ring_class,
        mass_mt=mass_mt,
        inner_rad=inner_rad,
        outer_rad=outer_rad,
    )


def get_system_detail(db_paths: list[str], system_name: str) -> Optional[SystemDetail]:
    """Looks up one system by exact name across db_paths, first match wins
    (a given system's body/ring data shouldn't legitimately differ across
    sector DBs, so there's nothing to merge). Returns None if system_name
    isn't found in any valid DB."""
    if not system_name:
        return None
    for db_path in db_paths:
        if not db_path or not Path(db_path).exists():
            continue
        conn = sqlite3.connect(db_path)
        try:
            system_row = conn.execute(
                "SELECT system_key, x, y, z FROM systems WHERE name = ? LIMIT 1",
                (system_name,),
            ).fetchone()
            if system_row is None:
                continue
            system_key, x, y, z = system_row

            body_rows = conn.execute(
                "SELECT body_name, body_type, sub_type, terraform_state, raw_json "
                "FROM bodies WHERE system_key = ? ORDER BY body_id",
                (system_key,),
            ).fetchall()
            bodies = [
                _parse_body_detail(body_name, body_type, sub_type, terraform_state, raw_json)
                for body_name, body_type, sub_type, terraform_state, raw_json in body_rows
            ]

            ring_rows = conn.execute(
                "SELECT body_name, ring_name, raw_json FROM rings WHERE system_key = ?",
                (system_key,),
            ).fetchall()
            rings = [
                _parse_ring_detail(body_name, ring_name, raw_json)
                for body_name, ring_name, raw_json in ring_rows
            ]

            return SystemDetail(system_name=system_name, x=x, y=y, z=z, bodies=bodies, rings=rings)
        finally:
            conn.close()
    return None
