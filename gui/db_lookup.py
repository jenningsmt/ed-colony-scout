"""Read-only sqlite helpers backing the Run tab's DB/origin-system picker."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_SECTOR_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "sector_library"


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
