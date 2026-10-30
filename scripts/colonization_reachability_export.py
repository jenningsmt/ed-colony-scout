#!/usr/bin/env python3
"""
colonization_reachability_export.py — Colonization candidate ranking within a
jump-chain network of an origin system.
=============================================================================
Finds every system reachable from a chosen origin system via a chain of
jumps of at most --max-jump-ly each (colonization-bridge style: each hop
must land within range of the *previous* system in the chain, not a single
radius around the origin), capped at --max-hops links in the chain. Then
ranks that reachable set for colonization value across four factors:
Earth-like Worlds, Water Worlds, Terraformable High Metal Content Worlds,
and high-density Metallic/Icy rings.

Multiple --db paths may be given (repeat the flag). Their systems are
merged into one reachability graph before the BFS runs, since routing
through an adjacent sector's systems can shorten a hop chain or make an
otherwise-unreachable candidate reachable. system_key is globally unique
(keyed on Elite Dangerous's own SystemAddress -- see
extract_sector_systems_to_sqlite.system_key_for), so merging is safe: a
system_key seen again from a later DB is a true cross-DB duplicate and is
skipped, keeping the earlier DB's copy.

Systems already claimed by a faction (systems.raw_json `government` field
set to anything other than absent/"None") are kept in the reachability
graph as hop-through waypoints but excluded from both output rankings,
since an occupied system is not a valid colonization target. Systems whose
stored metadata can't be read are treated the same way: unknown is not
eligible. Systems without a valid stored position are left out of the
graph and counted.

The point values driving colonization_score (tier sizes and per-body/ring
points) are configurable via the ScoringRubric dataclass -- see
gui/rubric.py for the GUI's editor. RING_DENSITY_FLOOR, the ring
ratio-bonus curve slope, and GALACTIC_MEDIANS stay fixed constants, not
part of the rubric (see ScoringRubric's docstring for why).

Writes two CSVs into --out-dir:
  1. `<sector>_<origin>_colonization_candidates.csv` -- every reachable,
     colonization-eligible system, ranked by colonization_score, including
     total_body_count (Star + Planet, matching Spansh's own bodyCount
     semantics) and every individual sub-score plus a key_scoring_factors
     summary column.
  2. `<sector>_<origin>_densest_systems.csv` -- the --dense-top-n (default
     20) reachable systems with the most planetary bodies, with primary
     stellar type, additional star count, a planetary-type breakdown, and
     every individual Metallic/Icy ring's MOI (surface density, kg/m^2) and
     % delta vs the galactic median for its class.

Entirely offline -- everything needed lives in the extracted sector DB(s)
(no EDSM/Spansh calls).

Usage:
    python scripts/colonization_reachability_export.py \\
        --db data/sector_library/sector_eafots.sqlite \\
        --db data/sector_library/sector_Parrots_Head_Sector.sqlite \\
        --sector "Eafots + Parrots Head Sector" \\
        --origin-system "Eafots VB-W b15-1" \\
        --max-jump-ly 15 --max-hops 15 \\
        --out-dir out
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional

try:
    from scripts.extract_sector_systems_to_sqlite import sanitize_prefix
except ImportError:
    from extract_sector_systems_to_sqlite import sanitize_prefix


# ── Ring density reference (same constants as sector_survey.py) ────────────

GALACTIC_MEDIANS: dict[str, float] = {
    "Icy":      8.591068e-06,
    "Metallic": 9.251326e-06,
}

# A ring only counts as "high-density" at or above this multiple of the
# galactic median surface density for its class. Fixed -- tied to how the
# median itself is computed, not a scoring preference.
RING_DENSITY_FLOOR = 1.0

SUBTYPE_ELW = "Earth-like world"
SUBTYPE_WW  = "Water world"
SUBTYPE_HMC = "High metal content world"
TERRAFORM_YES = "Terraformable"


# ── Scoring rubric ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoringRubric:
    """User-editable colonization-scoring rubric (Run tab's "Customize
    Scoring..." dialog in the GUI; CLI usage always uses STOCK_RUBRIC).
    RING_DENSITY_FLOOR, the 0.5 ratio-bonus slope in score_ring(), and
    GALACTIC_MEDIANS stay fixed module-level constants -- they're tied to
    how the galactic median itself is computed, not a preference."""
    elw_first_n: int = 2
    elw_first_pts: float = 100.0
    elw_rest_pts: float = 50.0
    ww_first_n: int = 2
    ww_first_pts: float = 40.0
    ww_rest_pts: float = 20.0
    hmct_first_n: int = 3
    hmct_first_pts: float = 30.0
    hmct_rest_pts: float = 15.0
    ring_metallic_base_pts: float = 30.0
    ring_icy_base_pts: float = 25.0


STOCK_RUBRIC = ScoringRubric()


ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNKNOWN = "unknown"


def colonization_status(raw_json: Optional[str]) -> str:
    """ELIGIBLE if the system has no established government -- systems.
    raw_json's `government` field is absent or the literal string "None".
    Any other value (Democracy, Feudal, Dictatorship, Cooperative, ...)
    means it's already inhabited/claimed by a faction: INELIGIBLE. Missing,
    malformed or non-object raw_json is UNKNOWN, never eligible.

    An absent government field in a well-formed record is the dump's best
    available signal, not proof of current in-game status. This rule is
    fixed and not part of ScoringRubric -- it's a game-mechanic fact, not a
    scoring preference."""
    if not raw_json:
        return UNKNOWN
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return UNKNOWN
    if not isinstance(data, dict):
        return UNKNOWN
    gov = data.get("government")
    return ELIGIBLE if gov is None or gov == "None" else INELIGIBLE


def is_colonization_eligible(raw_json: Optional[str]) -> bool:
    return colonization_status(raw_json) == ELIGIBLE


# ── Body/ring scoring ────────────────────────────────────────────────────

def score_tiered(count: int, first_n: int, first_pts: float, rest_pts: float) -> float:
    """First `first_n` occurrences worth `first_pts` each, remainder `rest_pts` each."""
    total = 0.0
    for i in range(count):
        total += first_pts if i < first_n else rest_pts
    return total


def ring_density(mass: float, inner_m: float, outer_m: float) -> float:
    if mass <= 0 or outer_m <= inner_m or inner_m <= 0:
        return 0.0
    area = math.pi * (outer_m ** 2 - inner_m ** 2)
    return mass / area if area > 0 else 0.0


def score_ring(ratio: float, ring_class: str, rubric: ScoringRubric) -> float:
    base = rubric.ring_metallic_base_pts if ring_class == "Metallic" else rubric.ring_icy_base_pts
    return base * (1.0 + (ratio - 1.0) * 0.5)


KEY_FACTOR_ORDER = ["elw_score", "ww_score", "hmct_score", "metallic_ring_score", "icy_ring_score"]
KEY_FACTOR_ABBREV = {
    "elw_score": "ELW", "ww_score": "WW", "hmct_score": "HMC-T",
    "metallic_ring_score": "Metallic", "icy_ring_score": "Icy",
}


def build_key_scoring_factors(scores: dict[str, float]) -> str:
    """All nonzero-contributing categories, abbreviated, ordered by point
    contribution descending (ties broken by KEY_FACTOR_ORDER for
    determinism). Empty string if the system scored zero everywhere."""
    nonzero = [(k, scores.get(k) or 0.0) for k in KEY_FACTOR_ORDER if (scores.get(k) or 0.0) > 0]
    nonzero.sort(key=lambda kv: -kv[1])
    return "+".join(KEY_FACTOR_ABBREV[k] for k, _ in nonzero)


# ── Reachability graph ───────────────────────────────────────────────────

def build_adjacency(
    coords: list[tuple[float, float, float]],
    max_jump_ly: float,
) -> dict[int, list[int]]:
    """Grid-bucketed spatial adjacency: edge between any two points within
    max_jump_ly of each other. Cell size = max_jump_ly, 27-neighbor-cell
    candidate check keeps this near-linear for realistically sparse sectors.
    Raises ValueError unless max_jump_ly is a finite number > 0."""
    if not (isinstance(max_jump_ly, (int, float)) and math.isfinite(max_jump_ly) and max_jump_ly > 0):
        raise ValueError(f"max jump must be a finite number of ly above 0, not {max_jump_ly!r}")
    cell = max_jump_ly
    grid: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for i, (x, y, z) in enumerate(coords):
        key = (int(x // cell), int(y // cell), int(z // cell))
        grid[key].append(i)

    max_jump_sq = max_jump_ly * max_jump_ly
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]

    adjacency: dict[int, list[int]] = defaultdict(list)

    def maybe_link(a: int, b: int) -> None:
        ax, ay, az = coords[a]
        bx, by, bz = coords[b]
        d2 = (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2
        if d2 <= max_jump_sq:
            adjacency[a].append(b)
            adjacency[b].append(a)

    for key, idxs in grid.items():
        kx, ky, kz = key
        for off in offsets:
            nkey = (kx + off[0], ky + off[1], kz + off[2])
            if nkey < key:
                continue  # each unordered cell pair processed once
            other_idxs = grid.get(nkey)
            if not other_idxs:
                continue
            if nkey == key:
                for pi in range(len(idxs)):
                    for pj in range(pi + 1, len(idxs)):
                        maybe_link(idxs[pi], idxs[pj])
            else:
                for a in idxs:
                    for b in other_idxs:
                        maybe_link(a, b)

    return adjacency


def bfs_hop_distances(
    adjacency: dict[int, list[int]],
    origin_idx: int,
    max_hops: int,
) -> dict[int, int]:
    """Hop distance from origin_idx to every node reachable within max_hops."""
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops < 0:
        raise ValueError(f"max hops must be a whole number of 0 or more, not {max_hops!r}")
    dist: dict[int, int] = {origin_idx: 0}
    q = deque([origin_idx])
    while q:
        cur = q.popleft()
        if dist[cur] >= max_hops:
            continue
        for nb in adjacency.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                q.append(nb)
    return dist


def distance_3d(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(a, b)))


# ── Multi-DB system loading ──────────────────────────────────────────────

class MergedSystems(NamedTuple):
    keys: list[str]
    names: list[str]
    coords: list[tuple[float, float, float]]
    eligible: list[bool]            # status == ELIGIBLE
    key_to_db_idx: dict[str, int]   # system_key -> index into db_paths
    status: list[str]               # ELIGIBLE / INELIGIBLE / UNKNOWN


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    """Open an existing sector DB read-only. Raises FileNotFoundError for a
    missing path instead of silently creating an empty database there."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Sector DB not found: {path}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _valid_position(x, y, z) -> Optional[tuple[float, float, float]]:
    try:
        point = (float(x), float(y), float(z))
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(v) for v in point) else None


def load_merged_systems(db_paths: list[Path]) -> MergedSystems:
    """Loads systems.(system_key,name,x,y,z,raw_json) from every db_path and
    concatenates into one global list, tagging each system_key with which
    db_paths index it came from. system_key is globally unique (keyed on
    ED's own SystemAddress -- see extract_sector_systems_to_sqlite's
    system_key_for), so a key seen again from a later DB is a true cross-DB
    duplicate (rare -- sector-prefix matching in extract_multi_sector_to_
    sqlite.py is mutually exclusive) and is skipped defensively, keeping
    the earlier DB's copy (db_paths order == precedence order).

    Rows without three finite coordinates are left out and counted: a
    system with no usable position can't be placed in the graph (which
    doesn't mean it isn't there in game)."""
    keys: list[str] = []
    names: list[str] = []
    coords: list[tuple[float, float, float]] = []
    status: list[str] = []
    key_to_db_idx: dict[str, int] = {}
    seen: set[str] = set()

    for db_idx, db_path in enumerate(db_paths):
        conn = connect_read_only(db_path)
        try:
            rows = conn.execute(
                "SELECT system_key, name, x, y, z, raw_json FROM systems ORDER BY rowid"
            ).fetchall()
        finally:
            conn.close()
        dup_count = 0
        no_position = 0
        for key, name, x, y, z, raw_json in rows:
            point = _valid_position(x, y, z)
            if point is None:
                no_position += 1
                continue
            if key in seen:
                dup_count += 1
                continue
            seen.add(key)
            keys.append(key)
            names.append(name)
            coords.append(point)
            status.append(colonization_status(raw_json))
            key_to_db_idx[key] = db_idx
        notes = []
        if dup_count:
            notes.append(f"{dup_count} skipped as cross-DB duplicates")
        if no_position:
            notes.append(f"{no_position} without a valid position, left out")
        print(
            f"  {db_path.name}: {len(rows) - no_position:,} systems"
            + (f" ({'; '.join(notes)})" if notes else "")
        )

    eligible = [s == ELIGIBLE for s in status]
    return MergedSystems(keys, names, coords, eligible, key_to_db_idx, status)


def _partition_by_db(keys: list[str], key_to_db_idx: dict[str, int]) -> dict[int, list[str]]:
    by_db: dict[int, list[str]] = defaultdict(list)
    for k in keys:
        by_db[key_to_db_idx[k]].append(k)
    return by_db


# ── Data extraction for the reachable set ───────────────────────────────

# Reachable sets can hold far more keys than SQLite allows as bound
# parameters in one statement (32,766 by default), so the keys are staged in
# a per-connection temp table and joined instead of listed in IN (?, ...).
_STAGED_KEYS = "(SELECT system_key FROM temp.wanted_keys)"


def _stage_keys(conn: sqlite3.Connection, keys: list[str]) -> None:
    """Replace the contents of temp.wanted_keys with `keys` (duplicates
    collapse). Works on read-only connections: temp tables live in a
    separate temporary database."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS wanted_keys (system_key TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.wanted_keys")
    conn.executemany("INSERT OR IGNORE INTO temp.wanted_keys VALUES (?)", ((k,) for k in keys))

def score_systems(
    conn: sqlite3.Connection,
    reachable_keys: list[str],
) -> dict[str, dict]:
    """Body/ring aggregation, scoped to just the reachable systems from ONE
    DB connection. Returns raw counts/ratios only -- point scoring (which
    depends on the active ScoringRubric) happens later in run()."""
    _stage_keys(conn, reachable_keys)
    results: dict[str, dict] = {
        k: {
            "elw_count": 0, "ww_count": 0, "hmct_count": 0,
            "metallic_ratio": 0.0, "icy_ratio": 0.0, "total_body_count": 0,
        }
        for k in reachable_keys
    }

    for system_key, body_type, sub_type, terraform_state in conn.execute(
        f"SELECT system_key, body_type, sub_type, terraform_state FROM bodies "
        f"WHERE system_key IN {_STAGED_KEYS}"
    ):
        r = results.get(system_key)
        if r is None:
            continue
        if body_type != "Barycentre":
            # Matches Spansh's own bodyCount semantics (Star + Planet, not
            # the non-physical orbital-pivot Barycentre rows).
            r["total_body_count"] += 1
        if sub_type == SUBTYPE_ELW:
            r["elw_count"] += 1
        elif sub_type == SUBTYPE_WW:
            r["ww_count"] += 1
        elif sub_type == SUBTYPE_HMC and terraform_state == TERRAFORM_YES:
            r["hmct_count"] += 1

    for system_key, raw_json in conn.execute(
        f"SELECT system_key, raw_json FROM rings "
        f"WHERE system_key IN {_STAGED_KEYS} AND raw_json IS NOT NULL"
    ):
        r = results.get(system_key)
        if r is None:
            continue
        try:
            d = json.loads(raw_json)
            rtype = (d.get("type") or "").strip()
            if rtype not in GALACTIC_MEDIANS:
                continue
            mass  = float(d.get("mass") or 0)
            inner = float(d.get("innerRadius") or 0)
            outer = float(d.get("outerRadius") or 0)
            dens = ring_density(mass, inner, outer)
            if dens <= 0:
                continue
            ratio = dens / GALACTIC_MEDIANS[rtype]
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

        if ratio < RING_DENSITY_FLOOR:
            continue
        key = "metallic_ratio" if rtype == "Metallic" else "icy_ratio"
        if ratio > r[key]:
            r[key] = ratio

    return results


def score_systems_multi(
    db_paths: list[Path],
    reachable_keys: list[str],
    key_to_db_idx: dict[str, int],
) -> dict[str, dict]:
    """Partitions reachable_keys by source DB and calls the unchanged
    score_systems() once per DB, merging results (collision-free -- keys
    are globally unique)."""
    merged: dict[str, dict] = {}
    for db_idx, sub_keys in _partition_by_db(reachable_keys, key_to_db_idx).items():
        conn = connect_read_only(db_paths[db_idx])
        try:
            merged.update(score_systems(conn, sub_keys))
        finally:
            conn.close()
    return merged


def extract_dense_system_details(
    conn: sqlite3.Connection,
    reachable_keys: list[str],
) -> dict[str, dict]:
    """Per-system detail needed for the densest-systems report: primary star
    type, count of additional stars, planetary body type breakdown, and
    every individual Metallic/Icy ring (no density floor -- this report is
    descriptive, not a qualification filter) with its MOI (surface density,
    kg/m^2, the same metric this project's sibling ring-density-monitor
    project calls MOI) and % delta vs the galactic median for its class."""
    _stage_keys(conn, reachable_keys)
    details: dict[str, dict] = {
        k: {"stars": [], "planet_types": [], "rings": []}
        for k in reachable_keys
    }

    for system_key, sub_type, raw_json in conn.execute(
        f"SELECT system_key, sub_type, raw_json FROM bodies "
        f"WHERE system_key IN {_STAGED_KEYS} AND body_type = 'Star'"
    ):
        d = details.get(system_key)
        if d is None:
            continue
        is_main = False
        distance_to_arrival = math.inf
        try:
            data = json.loads(raw_json) if raw_json else {}
            is_main = data.get("mainStar") is True
            distance_to_arrival = float(data.get("distanceToArrival") or math.inf)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        d["stars"].append({
            "sub_type": sub_type or "Unknown",
            "is_main": is_main,
            "distance_to_arrival": distance_to_arrival,
        })

    for system_key, sub_type in conn.execute(
        f"SELECT system_key, sub_type FROM bodies "
        f"WHERE system_key IN {_STAGED_KEYS} AND body_type = 'Planet'"
    ):
        d = details.get(system_key)
        if d is None:
            continue
        d["planet_types"].append(sub_type or "Unknown")

    for system_key, ring_name, raw_json in conn.execute(
        f"SELECT system_key, ring_name, raw_json FROM rings "
        f"WHERE system_key IN {_STAGED_KEYS} AND raw_json IS NOT NULL"
    ):
        d = details.get(system_key)
        if d is None:
            continue
        try:
            rd = json.loads(raw_json)
            rtype = (rd.get("type") or "").strip()
            if rtype not in GALACTIC_MEDIANS:
                continue
            mass  = float(rd.get("mass") or 0)
            inner = float(rd.get("innerRadius") or 0)
            outer = float(rd.get("outerRadius") or 0)
            moi = ring_density(mass, inner, outer)
            if moi <= 0:
                continue
            ratio = moi / GALACTIC_MEDIANS[rtype]
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        d["rings"].append({
            "ring_name": ring_name,
            "ring_class": rtype,
            "moi": moi,
            "delta_pct": (ratio - 1.0) * 100.0,
        })

    # Resolve primary star + summarize per system
    resolved: dict[str, dict] = {}
    for system_key, d in details.items():
        stars = d["stars"]
        primary_type = ""
        if stars:
            mains = [s for s in stars if s["is_main"]]
            primary = mains[0] if mains else min(stars, key=lambda s: s["distance_to_arrival"])
            primary_type = primary["sub_type"]
        additional_star_count = max(len(stars) - 1, 0)

        planet_counts: dict[str, int] = defaultdict(int)
        for pt in d["planet_types"]:
            planet_counts[pt] += 1
        breakdown = ", ".join(
            f"{cnt}x {pt}" for pt, cnt in sorted(planet_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )

        rings_sorted = sorted(d["rings"], key=lambda r: -r["moi"])
        rings_str = "; ".join(
            f"{r['ring_name']} ({r['ring_class']}): MOI={r['moi']:.3e} kg/m^2, "
            f"delta={r['delta_pct']:+.1f}% vs galactic norm"
            for r in rings_sorted
        )

        resolved[system_key] = {
            "primary_stellar_type": primary_type,
            "additional_stellar_count": additional_star_count,
            "planetary_body_count": len(d["planet_types"]),
            "planetary_body_breakdown": breakdown,
            "metallic_icy_rings": rings_str,
        }

    return resolved


def extract_dense_system_details_multi(
    db_paths: list[Path],
    reachable_keys: list[str],
    key_to_db_idx: dict[str, int],
) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for db_idx, sub_keys in _partition_by_db(reachable_keys, key_to_db_idx).items():
        conn = connect_read_only(db_paths[db_idx])
        try:
            merged.update(extract_dense_system_details(conn, sub_keys))
        finally:
            conn.close()
    return merged


def write_dense_systems_csv(
    out_path: Path,
    reachable_idxs: list[int],
    keys: list[str],
    names: list[str],
    coords: list[tuple[float, float, float]],
    dist: dict[int, int],
    origin_coords: tuple[float, float, float],
    scored: dict[str, dict],
    dense_details: dict[str, dict],
    top_n: int,
) -> Path:
    rows = []
    for i in reachable_idxs:
        key = keys[i]
        details = dense_details[key]
        rows.append({
            "system_name": names[i],
            "distance_ly": round(distance_3d(origin_coords, coords[i]), 2),
            "hop_count": dist[i],
            "total_body_count": scored[key]["total_body_count"],
            "primary_stellar_type": details["primary_stellar_type"],
            "additional_stellar_count": details["additional_stellar_count"],
            "planetary_body_count": details["planetary_body_count"],
            "planetary_body_breakdown": details["planetary_body_breakdown"],
            "metallic_icy_rings": details["metallic_icy_rings"],
        })

    rows.sort(key=lambda r: (-r["planetary_body_count"], r["system_name"]))
    rows = rows[:top_n]
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank

    fieldnames = [
        "rank", "system_name", "distance_ly", "hop_count", "total_body_count",
        "primary_stellar_type", "additional_stellar_count",
        "planetary_body_count", "planetary_body_breakdown",
        "metallic_icy_rings",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"\nCSV written: {out_path}  ({len(rows)} rows)")
    print(f"\nTop {min(10, len(rows))} densest systems:")
    for r in rows[:10]:
        print(f"  #{r['rank']:<3} {r['system_name']:<28} planets={r['planetary_body_count']:<3} "
              f"primary={r['primary_stellar_type']:<24} hops={r['hop_count']}")

    return out_path


# ── Main pipeline ────────────────────────────────────────────────────────

def run(
    db_paths: list[Path],
    origin_system: str,
    max_jump_ly: float,
    max_hops: int,
    out_dir: Path,
    sector_label: str,
    dense_top_n: int = 20,
    rubric: ScoringRubric = STOCK_RUBRIC,
) -> tuple[Path, Path]:
    print("Colonization Reachability Export")
    print(f"  Source DB(s):  {', '.join(str(p) for p in db_paths)}")
    print(f"  Origin system: {origin_system}")
    print(f"  Max jump:      {max_jump_ly} ly/hop")
    print(f"  Max hops:      {max_hops}")
    print()

    print("Loading systems...")
    keys, names, coords, eligible, key_to_db_idx, status = load_merged_systems(db_paths)
    n = len(keys)
    print(f"  {n:,} systems loaded total.")

    # First-occurrence-wins (a plain dict comprehension would give
    # *last*-wins, disagreeing with load_merged_systems' dedup precedence).
    name_to_idx: dict[str, int] = {}
    for i, name in enumerate(names):
        name_to_idx.setdefault(name, i)
    origin_idx = name_to_idx.get(origin_system)
    if origin_idx is None:
        raise ValueError(f"Origin system {origin_system!r} not found in any selected DB")

    print("Building spatial adjacency graph...")
    adjacency = build_adjacency(coords, max_jump_ly)
    edge_count = sum(len(v) for v in adjacency.values()) // 2
    print(f"  {edge_count:,} edges (<= {max_jump_ly} ly apart).")

    print(f"Running BFS from origin (cap {max_hops} hops)...")
    dist = bfs_hop_distances(adjacency, origin_idx, max_hops)
    print(f"  {len(dist):,} systems reachable within {max_hops} hops.")

    reachable_idxs = sorted(dist.keys())
    reachable_keys = [keys[i] for i in reachable_idxs]

    # Systems stay in the reachability graph as hop-through waypoints
    # regardless of ownership, but only ungoverned ("None" government)
    # systems are valid colonization targets -- occupied systems, and
    # systems whose metadata can't be read, are excluded from both output
    # rankings below.
    eligible_idxs = [i for i in reachable_idxs if eligible[i]]
    occupied_count = sum(1 for i in reachable_idxs if status[i] == INELIGIBLE)
    unknown_count = sum(1 for i in reachable_idxs if status[i] == UNKNOWN)
    print(f"  {len(eligible_idxs):,} of those are colonization-eligible "
          f"(ungoverned); {occupied_count:,} excluded as already inhabited/claimed"
          + (f"; {unknown_count:,} excluded because their stored data couldn't be read." if unknown_count else "."))

    print("Scoring reachable systems (bodies + rings)...")
    scored = score_systems_multi(db_paths, reachable_keys, key_to_db_idx)

    print("Extracting stellar/planetary composition for densest-systems report...")
    dense_details = extract_dense_system_details_multi(db_paths, reachable_keys, key_to_db_idx)

    origin_coords = coords[origin_idx]
    out_rows = []
    for i in eligible_idxs:
        key = keys[i]
        name = names[i]
        x, y, z = coords[i]
        s = scored[key]

        elw_score  = score_tiered(s["elw_count"], rubric.elw_first_n, rubric.elw_first_pts, rubric.elw_rest_pts)
        ww_score   = score_tiered(s["ww_count"], rubric.ww_first_n, rubric.ww_first_pts, rubric.ww_rest_pts)
        hmct_score = score_tiered(s["hmct_count"], rubric.hmct_first_n, rubric.hmct_first_pts, rubric.hmct_rest_pts)

        metallic_score = score_ring(s["metallic_ratio"], "Metallic", rubric) if s["metallic_ratio"] > 0 else 0.0
        icy_score      = score_ring(s["icy_ratio"], "Icy", rubric) if s["icy_ratio"] > 0 else 0.0
        ring_score = metallic_score + icy_score

        total = elw_score + ww_score + hmct_score + ring_score

        key_scoring_factors = build_key_scoring_factors({
            "elw_score": elw_score, "ww_score": ww_score, "hmct_score": hmct_score,
            "metallic_ring_score": metallic_score, "icy_ring_score": icy_score,
        })

        out_rows.append({
            "system_name": name,
            "x": x, "y": y, "z": z,
            "distance_ly": round(distance_3d(origin_coords, (x, y, z)), 2),
            "hop_count": dist[i],
            "total_body_count": s["total_body_count"],
            "elw_count": s["elw_count"],
            "ww_count": s["ww_count"],
            "hmct_terraformable_count": s["hmct_count"],
            "elw_score": round(elw_score, 1),
            "ww_score": round(ww_score, 1),
            "hmct_score": round(hmct_score, 1),
            "metallic_ring_ratio": round(s["metallic_ratio"], 2) if s["metallic_ratio"] > 0 else "",
            "metallic_ring_score": round(metallic_score, 1) if metallic_score > 0 else "",
            "icy_ring_ratio": round(s["icy_ratio"], 2) if s["icy_ratio"] > 0 else "",
            "icy_ring_score": round(icy_score, 1) if icy_score > 0 else "",
            "ring_score": round(ring_score, 1),
            "colonization_score": round(total, 1),
            "key_scoring_factors": key_scoring_factors,
        })

    out_rows.sort(key=lambda r: (-r["colonization_score"], r["system_name"]))
    for rank, r in enumerate(out_rows, 1):
        r["rank"] = rank

    out_dir.mkdir(parents=True, exist_ok=True)
    slug = sanitize_prefix(origin_system)
    out_path = out_dir / f"{sanitize_prefix(sector_label)}_{slug}_colonization_candidates.csv"

    fieldnames = [
        "rank", "system_name", "x", "y", "z", "distance_ly", "hop_count",
        "total_body_count",
        "elw_count", "ww_count", "hmct_terraformable_count",
        "elw_score", "ww_score", "hmct_score",
        "metallic_ring_ratio", "metallic_ring_score",
        "icy_ring_ratio", "icy_ring_score", "ring_score",
        "colonization_score", "key_scoring_factors",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in out_rows:
            writer.writerow(r)

    print(f"\nCSV written: {out_path}  ({len(out_rows)} rows)")
    print("\nTop 10:")
    for r in out_rows[:10]:
        print(f"  #{r['rank']:<3} {r['system_name']:<28} score={r['colonization_score']:<7} "
              f"ELW={r['elw_count']} WW={r['ww_count']} HMC-T={r['hmct_terraformable_count']} "
              f"hops={r['hop_count']}")

    print(f"\nBuilding densest-systems report (top {dense_top_n} by planetary body count)...")
    dense_out_path = out_dir / f"{sanitize_prefix(sector_label)}_{slug}_densest_systems.csv"
    write_dense_systems_csv(
        out_path=dense_out_path,
        reachable_idxs=eligible_idxs,
        keys=keys,
        names=names,
        coords=coords,
        dist=dist,
        origin_coords=origin_coords,
        scored=scored,
        dense_details=dense_details,
        top_n=dense_top_n,
    )

    return out_path, dense_out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rank colonization candidates within a jump-chain network of an origin system."
    )
    parser.add_argument(
        "--db", dest="db_paths", action="append", type=Path, required=True,
        help="Path to a sector library SQLite file; repeat --db to merge multiple sectors' reachability graphs",
    )
    parser.add_argument("--sector", required=True, help="Sector name (for labeling/output filename)")
    parser.add_argument("--origin-system", required=True, help="Origin system name (must exist in one of the DBs)")
    parser.add_argument("--max-jump-ly", type=float, default=15.0, help="Max ly per hop (default: 15)")
    parser.add_argument("--max-hops", type=int, default=15, help="Max chain length in hops (default: 15)")
    parser.add_argument("--dense-top-n", type=int, default=20,
                         help="Number of systems in the densest-systems (planetary body count) report (default: 20)")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Output directory (default: out)")
    args = parser.parse_args()

    missing = [p for p in args.db_paths if not p.exists()]
    if missing:
        print(f"ERROR: DB(s) not found: {', '.join(str(p) for p in missing)}", file=sys.stderr)
        return 1

    try:
        run(
            db_paths=args.db_paths,
            origin_system=args.origin_system,
            max_jump_ly=args.max_jump_ly,
            max_hops=args.max_hops,
            out_dir=args.out_dir,
            sector_label=args.sector,
            dense_top_n=args.dense_top_n,
            rubric=STOCK_RUBRIC,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
