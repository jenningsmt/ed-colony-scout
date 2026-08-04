"""Read-only sqlite helpers backing the Run tab's DB/origin-system picker."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_SECTOR_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "data" / "sector_library"


def derive_sector_label(db_path: str) -> str:
    """Best-effort sector label from a sector DB filename, e.g.
    'sector_Parrots_Head_Sector.sqlite' -> 'Parrots Head Sector'. Only used
    to pre-fill the Sector Label field -- exact spelling doesn't matter,
    since it only feeds sanitize_prefix() for output filenames."""
    stem = Path(db_path).stem
    if stem.startswith("sector_"):
        stem = stem[len("sector_"):]
    return stem.replace("_", " ").strip()


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_system_names(db_path: str, query: str, limit: int = 50) -> list[str]:
    """Case-insensitive substring search over systems.name. Returns [] for
    an empty query -- never used to dump the full table into a dropdown."""
    query = query.strip()
    if not query or not db_path or not Path(db_path).exists():
        return []
    like = f"%{_escape_like(query)}%"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM systems WHERE LOWER(name) LIKE LOWER(?) ESCAPE '\\' "
            "ORDER BY name LIMIT ?",
            (like, limit),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def system_exists(db_path: str, name: str) -> bool:
    if not name or not db_path or not Path(db_path).exists():
        return False
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT 1 FROM systems WHERE name = ? LIMIT 1", (name,)).fetchone()
    finally:
        conn.close()
    return row is not None
