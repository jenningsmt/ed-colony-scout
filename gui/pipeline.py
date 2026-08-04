"""Wraps scripts/colonization_reachability_export.run() for the GUI worker.

run() itself is synchronous and non-interruptible (load systems -> build
adjacency -> BFS -> score -> write two CSVs -- sub-second even against a
136k-system sector DB), so cancellation is only checked around the call,
never inside it: a Cancel click either stops the run before it starts, or
lets an already-started run finish writing its CSVs but skips loading the
results into the GUI.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from scripts import colonization_reachability_export

# Extends worker.py's DONE_SENTINEL convention with a second out-of-band
# line: printed (and so queued via the worker's stdout redirect) right
# before a successful return, carrying the two output CSV paths back to the
# GUI thread without a second IPC channel. "|" is illegal in Windows paths,
# so it's a safe separator; the sentinel/payload split uses str.split(":", 1),
# safe even though the paths themselves contain a drive-letter colon, since
# only the first colon (right after the sentinel) is split on.
RESULT_SENTINEL = "__reachability_result__"
RESULT_SEP = "|"


def _cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def run_reachability_pipeline(
    config: dict[str, Any],
    cancel_event: Optional[threading.Event] = None,
    confirm_cb: Optional[Callable[[dict], bool]] = None,
) -> int:
    """Validate config, run the reachability/scoring export, and (on
    success) print a RESULT_SENTINEL line carrying the two output CSV
    paths. Returns 0 on success, 1 on a validation/run error, 130 if
    cancelled. confirm_cb is accepted (worker.py always passes it) but
    unused -- there's no large-run budget warning for this pipeline."""
    db_path = str(config.get("db_path") or "").strip()
    sector_label = str(config.get("sector_label") or "").strip()
    origin_system = str(config.get("origin_system") or "").strip()

    if not db_path or not Path(db_path).exists():
        print(f"ERROR: sector DB not found: {db_path or '(not set)'}")
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

    try:
        candidates_path, dense_path = colonization_reachability_export.run(
            db_path=Path(db_path),
            origin_system=origin_system,
            max_jump_ly=max_jump_ly,
            max_hops=max_hops,
            out_dir=out_dir,
            sector_label=sector_label,
            dense_top_n=dense_top_n,
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
