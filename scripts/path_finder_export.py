#!/usr/bin/env python3
"""
path_finder_export.py — Shortest-path and reachability-from-occupied-systems
queries over the same jump-chain reachability graph used by
colonization_reachability_export.py.
=============================================================================
Two independent query modes, both reusing load_merged_systems/build_adjacency/
distance_3d rather than duplicating graph-building logic:

  1. Shortest Path -- true shortest hop-chain path between two named systems
     (unbounded BFS -- a shortest path either exists in the loaded/connected
     data or it doesn't; a hop cap makes no sense for this question).
     Uninhabited systems are valid hop-through waypoints, same as the
     existing reachability pipeline.

  2. Reachable? -- given a target system, a hop cap (or none), and a
     candidate count N, finds the N nearest ALREADY-OCCUPIED systems to the
     target by straight-line distance (occupied = has an established
     government -- modeling the real game mechanic that launching a new
     colony bridge requires starting from an already-inhabited system, not
     necessarily the player's own colony), then reports whichever of those
     N reaches the target in the fewest hops within the cap, or the
     closest miss if none qualify.

Efficiency note: build_adjacency's graph is UNDIRECTED (every edge is added
both ways), so hop_distance(candidate -> target) == hop_distance(target ->
candidate). check_reachable() therefore runs exactly ONE unbounded BFS
rooted at the TARGET, then looks up each candidate's hop distance in that
single result -- never one BFS per candidate.

Usage:
    python scripts/path_finder_export.py \\
        --db data/sector_library/sector_Parrots_Head_Sector.sqlite \\
        shortest-path --origin-system "Parrot's Head Sector EL-Y d70" \\
        --target-system "Parrot's Head Sector AF-A d35" \\
        --max-jump-ly 15 --out-dir out

    python scripts/path_finder_export.py \\
        --db data/sector_library/sector_Parrots_Head_Sector.sqlite \\
        reachable --target-system "Parrot's Head Sector AF-A d35" \\
        --max-jump-ly 15 --max-hops 20 --candidates 10 --out-dir out
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import deque
from pathlib import Path
from typing import Optional

try:
    from scripts.colonization_reachability_export import (
        INELIGIBLE,
        build_adjacency,
        distance_3d,
        load_merged_systems,
    )
    from scripts.extract_sector_systems_to_sqlite import sanitize_prefix
except ImportError:
    from colonization_reachability_export import (
        INELIGIBLE,
        build_adjacency,
        distance_3d,
        load_merged_systems,
    )
    from extract_sector_systems_to_sqlite import sanitize_prefix


# ── BFS with parent tracking ─────────────────────────────────────────────

def bfs_with_parents(
    adjacency: dict[int, list[int]],
    root_idx: int,
    max_hops: Optional[int] = None,
) -> tuple[dict[int, int], dict[int, Optional[int]]]:
    """Same traversal as colonization_reachability_export.bfs_hop_distances
    but also tracks a parent pointer per visited node (root's own parent is
    None), so a path back to the root can be reconstructed afterward.
    max_hops=None means unbounded -- explore the whole connected component."""
    if max_hops is not None and (isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops < 0):
        raise ValueError(f"max hops must be a whole number of 0 or more, not {max_hops!r}")
    dist: dict[int, int] = {root_idx: 0}
    parent: dict[int, Optional[int]] = {root_idx: None}
    q = deque([root_idx])
    while q:
        cur = q.popleft()
        if max_hops is not None and dist[cur] >= max_hops:
            continue
        for nb in adjacency.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                parent[nb] = cur
                q.append(nb)
    return dist, parent


def reconstruct_path(parent: dict[int, Optional[int]], node_idx: int) -> list[int]:
    """Walks the parent chain from node_idx back to the BFS root (whose
    parent is None), returning ROOT-to-node_idx order. Callers whose root
    isn't the desired path start (check_reachable's target-rooted BFS) must
    reverse the result themselves -- see check_reachable() below."""
    path = [node_idx]
    cur = node_idx
    while parent[cur] is not None:
        cur = parent[cur]
        path.append(cur)
    path.reverse()
    return path


# ── Name lookup / candidate selection ────────────────────────────────────

def _build_name_index(names: list[str]) -> dict[str, int]:
    """First-occurrence-wins name -> index map. A plain dict comprehension
    would give *last*-wins, which would disagree with load_merged_systems'
    cross-DB dedup precedence (db_paths order == precedence order). Copied
    from colonization_reachability_export.run()'s own reasoning."""
    name_to_idx: dict[str, int] = {}
    for i, name in enumerate(names):
        name_to_idx.setdefault(name, i)
    return name_to_idx


def _nearest_occupied(
    target_idx: int,
    coords: list[tuple[float, float, float]],
    occupied: list[bool],
    count: int,
) -> list[int]:
    """Indices of the `count` nearest occupied (inhabited/claimed; not
    merely of unknown status) systems to target_idx by straight-line
    distance, ascending, excluding
    target_idx itself. Simple O(n) scan + sort -- an interactive one-off
    query, not perf-critical, no spatial index needed (unlike the
    hop-adjacency grid, which IS reused as-is via build_adjacency)."""
    target_coords = coords[target_idx]
    occupied_idxs = [i for i in range(len(coords)) if occupied[i] and i != target_idx]
    occupied_idxs.sort(key=lambda i: distance_3d(target_coords, coords[i]))
    return occupied_idxs[:count]


# ── Shared row-shape / CSV helpers ───────────────────────────────────────

def _path_rows(
    path_idxs: list[int],
    names: list[str],
    coords: list[tuple[float, float, float]],
    occupied: list[bool],
) -> list[dict]:
    """Shared path row shape (hop, system_name, x, y, z, leg_distance_ly,
    occupied) for a path already in launch-point-first order. Hop 0's
    leg_distance_ly is 0.0 (no previous system)."""
    rows = []
    prev_coords: Optional[tuple[float, float, float]] = None
    for hop, idx in enumerate(path_idxs):
        x, y, z = coords[idx]
        leg = distance_3d(prev_coords, (x, y, z)) if prev_coords is not None else 0.0
        rows.append({
            "hop": hop,
            "system_name": names[idx],
            "x": x, "y": y, "z": z,
            "leg_distance_ly": round(leg, 2),
            "occupied": occupied[idx],
        })
        prev_coords = (x, y, z)
    return rows


PATH_FIELDNAMES = ["hop", "system_name", "x", "y", "z", "leg_distance_ly", "occupied"]
CANDIDATE_FIELDNAMES = ["system_name", "straight_line_ly", "hop_count", "within_cap"]


def _write_path_csv(out_path: Path, rows: list[dict]) -> None:
    """Writes a copy of each row with occupied formatted Yes/No for
    readability -- the caller's in-memory dicts (destined for the
    JSON-sentinel result) keep real booleans, untouched."""
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PATH_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = dict(r)
            row["occupied"] = "Yes" if row["occupied"] else "No"
            writer.writerow(row)


def _write_candidates_csv(out_path: Path, rows: list[dict]) -> None:
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            row = dict(r)
            row["hop_count"] = row["hop_count"] if row["hop_count"] is not None else "unreachable"
            row["within_cap"] = "Yes" if row["within_cap"] else "No"
            writer.writerow(row)


# ── Orchestration ─────────────────────────────────────────────────────

def shortest_path(
    db_paths: list[Path],
    origin_system: str,
    target_system: str,
    max_jump_ly: float,
    out_dir: Optional[Path] = None,
) -> dict:
    print("Path Finder -- Shortest Path")
    print(f"  Source DB(s):  {', '.join(str(p) for p in db_paths)}")
    print(f"  Origin:        {origin_system}")
    print(f"  Target:        {target_system}")
    print(f"  Max jump:      {max_jump_ly} ly/hop")
    print()

    print("Loading systems...")
    keys, names, coords, eligible, key_to_db_idx, status = load_merged_systems(db_paths)
    occupied = [st == INELIGIBLE for st in status]
    print(f"  {len(keys):,} systems loaded total.")

    name_to_idx = _build_name_index(names)
    origin_idx = name_to_idx.get(origin_system)
    if origin_idx is None:
        raise ValueError(f"Origin system {origin_system!r} not found in any selected DB")
    target_idx = name_to_idx.get(target_system)
    if target_idx is None:
        raise ValueError(f"Target system {target_system!r} not found in any selected DB")

    print("Building spatial adjacency graph...")
    adjacency = build_adjacency(coords, max_jump_ly)
    edge_count = sum(len(v) for v in adjacency.values()) // 2
    print(f"  {edge_count:,} edges (<= {max_jump_ly} ly apart).")

    print("Running unbounded BFS from origin...")
    dist, parent = bfs_with_parents(adjacency, origin_idx, max_hops=None)

    if target_idx not in dist:
        print(f"  {target_system!r} is not connected to {origin_system!r} in the loaded data.")
        return {
            "reachable": False, "origin": origin_system, "target": target_system,
            "hop_count": None, "path": [],
        }

    hop_count = dist[target_idx]
    path_idxs = reconstruct_path(parent, target_idx)  # BFS root == origin: already origin-to-target order
    path_rows = _path_rows(path_idxs, names, coords, occupied)
    print(f"  Found: {hop_count} hop(s).")

    result = {
        "reachable": True, "origin": origin_system, "target": target_system,
        "hop_count": hop_count, "path": path_rows,
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{sanitize_prefix(origin_system)}_to_{sanitize_prefix(target_system)}_shortest_path.csv"
        _write_path_csv(out_path, path_rows)
        print(f"\nCSV written: {out_path}  ({len(path_rows)} rows)")

    return result


def check_reachable(
    db_paths: list[Path],
    target_system: str,
    max_jump_ly: float,
    max_hops: Optional[int],
    candidate_count: int,
    out_dir: Optional[Path] = None,
) -> dict:
    print("Path Finder -- Reachable?")
    print(f"  Source DB(s):  {', '.join(str(p) for p in db_paths)}")
    print(f"  Target:        {target_system}")
    print(f"  Max jump:      {max_jump_ly} ly/hop")
    print(f"  Max hops:      {'unlimited' if max_hops is None else max_hops}")
    print(f"  Candidates:    {candidate_count}")
    print()

    print("Loading systems...")
    keys, names, coords, eligible, key_to_db_idx, status = load_merged_systems(db_paths)
    occupied = [st == INELIGIBLE for st in status]
    print(f"  {len(keys):,} systems loaded total.")

    name_to_idx = _build_name_index(names)
    target_idx = name_to_idx.get(target_system)
    if target_idx is None:
        raise ValueError(f"Target system {target_system!r} not found in any selected DB")

    print("Building spatial adjacency graph...")
    adjacency = build_adjacency(coords, max_jump_ly)
    edge_count = sum(len(v) for v in adjacency.values()) // 2
    print(f"  {edge_count:,} edges (<= {max_jump_ly} ly apart).")

    print(f"Finding {candidate_count} nearest occupied system(s) to {target_system!r}...")
    candidate_idxs = _nearest_occupied(target_idx, coords, occupied, candidate_count)
    print(f"  {len(candidate_idxs)} candidate(s) found.")

    if not candidate_idxs:
        print("  No occupied systems exist anywhere in the loaded data -- nothing to check.")
        return {
            "reachable": False, "target": target_system, "max_hops": max_hops,
            "candidates": [], "best_candidate": None, "path": [], "closest_miss": None,
        }

    print("Running ONE unbounded BFS rooted at the target (graph is undirected, so "
          "hop_distance(candidate->target) == hop_distance(target->candidate) -- "
          "no need for one BFS per candidate)...")
    dist, parent = bfs_with_parents(adjacency, target_idx, max_hops=None)

    target_coords = coords[target_idx]
    candidates = []
    for idx in candidate_idxs:
        hop_count = dist.get(idx)
        within_cap = hop_count is not None and (max_hops is None or hop_count <= max_hops)
        candidates.append({
            "system_name": names[idx],
            "straight_line_ly": round(distance_3d(target_coords, coords[idx]), 2),
            "hop_count": hop_count,
            "within_cap": within_cap,
            "_idx": idx,  # internal only -- stripped before building the JSON-safe result
        })

    qualifying = [c for c in candidates if c["within_cap"]]
    best_candidate: Optional[dict] = None
    path_rows: list[dict] = []
    closest_miss: Optional[dict] = None

    if qualifying:
        # min() scans left-to-right and only replaces on strict '<', so ties
        # resolve to the candidate appearing first -- candidates is already
        # straight-line-distance-ascending from _nearest_occupied, so ties
        # naturally favor the physically nearer candidate. Intentional.
        winner = min(qualifying, key=lambda c: c["hop_count"])
        path_idxs = reconstruct_path(parent, winner["_idx"])
        path_idxs.reverse()  # BFS root == target: reconstruct gives target-to-candidate; reverse for launch-first presentation
        path_rows = _path_rows(path_idxs, names, coords, occupied)
        best_candidate = {k: v for k, v in winner.items() if k != "_idx"}
        print(f"  Winner: {winner['system_name']} ({winner['hop_count']} hop(s)).")
    else:
        connected = [c for c in candidates if c["hop_count"] is not None]
        if connected:
            closest = min(connected, key=lambda c: c["hop_count"])
            excess = None if max_hops is None else closest["hop_count"] - max_hops
            closest_miss = {
                "system_name": closest["system_name"],
                "hop_count": closest["hop_count"],
                "excess_hops": excess,
            }
            print(f"  No candidate within cap; closest miss: {closest['system_name']} "
                  f"({closest['hop_count']} hop(s)).")
        else:
            print("  None of the checked candidates are even connected to the target in the loaded data.")

    result = {
        "reachable": best_candidate is not None,
        "target": target_system,
        "max_hops": max_hops,
        "candidates": [{k: v for k, v in c.items() if k != "_idx"} for c in candidates],
        "best_candidate": best_candidate,
        "path": path_rows,
        "closest_miss": closest_miss,
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = sanitize_prefix(target_system)
        cand_path = out_dir / f"{slug}_reachable_candidates.csv"
        _write_candidates_csv(cand_path, result["candidates"])
        print(f"\nCSV written: {cand_path}  ({len(result['candidates'])} rows)")
        if path_rows:
            path_path = out_dir / f"{slug}_reachable_path.csv"
            _write_path_csv(path_path, path_rows)
            print(f"CSV written: {path_path}  ({len(path_rows)} rows)")

    return result


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shortest-path and reachability-from-occupied-systems queries over a sector reachability graph."
    )
    parser.add_argument(
        "--db", dest="db_paths", action="append", type=Path, required=True,
        help="Path to a sector library SQLite file; repeat --db to merge multiple sectors' reachability graphs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sp = subparsers.add_parser("shortest-path", help="Find the true shortest hop-chain path between two systems")
    sp.add_argument("--origin-system", required=True)
    sp.add_argument("--target-system", required=True)
    sp.add_argument("--max-jump-ly", type=float, default=15.0)
    sp.add_argument("--out-dir", type=Path, default=Path("out"))

    rp = subparsers.add_parser("reachable", help="Find the nearest occupied system that can reach a target within a hop cap")
    rp.add_argument("--target-system", required=True)
    rp.add_argument("--max-jump-ly", type=float, default=15.0)
    rp.add_argument("--max-hops", type=int, default=None, help="Omit for unlimited hops")
    rp.add_argument("--candidates", type=int, default=10)
    rp.add_argument("--out-dir", type=Path, default=Path("out"))

    args = parser.parse_args()

    missing = [p for p in args.db_paths if not p.exists()]
    if missing:
        print(f"ERROR: DB(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1

    try:
        if args.command == "shortest-path":
            shortest_path(
                db_paths=args.db_paths, origin_system=args.origin_system,
                target_system=args.target_system, max_jump_ly=args.max_jump_ly,
                out_dir=args.out_dir,
            )
        else:
            check_reachable(
                db_paths=args.db_paths, target_system=args.target_system,
                max_jump_ly=args.max_jump_ly, max_hops=args.max_hops,
                candidate_count=args.candidates, out_dir=args.out_dir,
            )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
