"""Wraps scripts/colonization_reachability_export.run() and
scripts/extract_multi_sector_to_sqlite.run() for the GUI worker.

The reachability run() is synchronous and non-interruptible (load systems
-> build adjacency -> BFS -> score -> write two CSVs -- a few seconds even
against a 136k-system sector DB), so cancellation is only checked around
the call, never inside it. The extraction run(), by contrast, is already
cancel_event-aware internally (checked mid-scan of the galaxy dump), which
matters since a full galaxy dump scan can run for a long time -- that
cancel_event is passed straight through.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from gui import rubric as rubric_module
from scripts import colonization_reachability_export, extract_multi_sector_to_sqlite

# Extends worker.py's DONE_SENTINEL convention with out-of-band result
# lines: printed (and so queued via the worker's stdout redirect) right
# before a successful return, carrying output paths back to the GUI thread
# without a second IPC channel. "|" is illegal in Windows paths, so it's a
# safe separator; the sentinel/payload split uses str.split(":", 1), safe
# even though the paths themselves contain a drive-letter colon, since only
# the first colon (right after the sentinel) is split on.
RESULT_SENTINEL = "__reachability_result__"
RESULT_SEP = "|"

EXTRACT_RESULT_SENTINEL = "__extraction_result__"
EXTRACT_RESULT_SEP = "|"


def _cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def run_reachability_pipeline(
    config: dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
    confirm_cb: Optional[Callable[[dict], bool]] = None,
) -> int:
    """Validate config, run the reachability/scoring export across every
    selected DB, and (on success) print a RESULT_SENTINEL line carrying the
    two output CSV paths. Returns 0 on success, 1 on a validation/run
    error, 130 if cancelled. confirm_cb is accepted (worker.py always
    passes it) but unused -- there's no large-run budget warning for this
    pipeline."""
    db_paths_raw = list(config.get("db_paths") or [])
    sector_label = str(config.get("sector_label") or "").strip()
    origin_system = str(config.get("origin_system") or "").strip()

    if not db_paths_raw:
        print("ERROR: at least one sector DB is required.")
        return 1
    missing = [p for p in db_paths_raw if not Path(p).exists()]
    if missing:
        print(f"ERROR: sector DB(s) not found: {', '.join(missing)}")
        return 1
    if not sector_label:
        print("ERROR: sector label is required.")
        return 1
    if not origin_system:
        print("ERROR: origin system is required.")
        return 1

    if _cancelled(cancel_event):
        print("Cancelled before starting.")
        return 130

    out_dir = Path(str(config.get("out_dir") or "out"))
    max_jump_ly = float(config.get("max_jump_ly", 15.0))
    max_hops = int(config.get("max_hops", 8))
    dense_top_n = int(config.get("dense_top_n", 20))
    rubric = rubric_module.rubric_from_dict(config.get("scoring_rubric") or {})

    try:
        candidates_path, dense_path = colonization_reachability_export.run(
            db_paths=[Path(p) for p in db_paths_raw],
            origin_system=origin_system,
            max_jump_ly=max_jump_ly,
            max_hops=max_hops,
            out_dir=out_dir,
            sector_label=sector_label,
            dense_top_n=dense_top_n,
            rubric=rubric,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    if _cancelled(cancel_event):
        print(
            "Cancelled after the run finished writing CSVs to disk; "
            "results will not be loaded into the tables."
        )
        return 130

    print(f"{RESULT_SENTINEL}:{candidates_path}{RESULT_SEP}{dense_path}")
    return 0


def run_sector_extraction_pipeline(
    config: dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
    confirm_cb: Optional[Callable[[dict], bool]] = None,
) -> int:
    """Validates config, runs extract_multi_sector_to_sqlite.run() (already
    cancel_event-aware internally), and on success prints an
    EXTRACT_RESULT_SENTINEL line carrying the deterministic per-prefix
    output DB paths so the GUI can auto-add them to the Run tab's DB
    selection."""
    galaxy_dump_path = str(config.get("galaxy_dump_path") or "").strip()
    prefixes = list(config.get("sector_prefixes") or [])
    sector_library_dir = str(config.get("sector_library_dir") or "").strip()

    if not galaxy_dump_path or not Path(galaxy_dump_path).exists():
        print(f"ERROR: galaxy dump not found: {galaxy_dump_path or '(not set)'}")
        return 1
    if not prefixes:
        print("ERROR: at least one sector prefix is required.")
        return 1
    if not sector_library_dir:
        print("ERROR: sector library directory is required.")
        return 1

    if _cancelled(cancel_event):
        print("Cancelled before starting.")
        return 130

    out_dir = Path(sector_library_dir)
    rc = extract_multi_sector_to_sqlite.run(
        input_path=Path(galaxy_dump_path),
        sector_prefixes=prefixes,
        output_dir=out_dir,
        cancel_event=cancel_event,
    )
    if rc != 0:
        return rc  # 130 (cancelled) or 1 (input/format error) propagate as-is

    db_paths = [
        str(out_dir / f"sector_{extract_multi_sector_to_sqlite.sanitize_prefix(p)}.sqlite")
        for p in prefixes
    ]
    print(f"{EXTRACT_RESULT_SENTINEL}:{EXTRACT_RESULT_SEP.join(db_paths)}")
    return 0
