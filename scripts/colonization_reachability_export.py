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

Systems already claimed by a faction (systems.raw_json `government` field
set to anything other than absent/"None") are kept in the reachability
graph as hop-through waypoints but excluded from both output rankings,
since an occupied system is not a valid colonization target.

Writes two CSVs into --out-dir:
  1. `<sector>_<origin>_colonization_candidates.csv` -- every reachable
     system, ranked by colonization_score, including total_body_count
     (Star + Planet, matching Spansh's own bodyCount semantics).
  2. `<sector>_<origin>_densest_systems.csv` -- the --dense-top-n (default
     20) reachable systems with the most planetary bodies, with primary
     stellar type, additional star count, a planetary-type breakdown, and
     every individual Metallic/Icy ring's MOI (surface density, kg/m^2) and
     % delta vs the galactic median for its class.

Entirely offline -- everything needed lives in the extracted sector DB
(no EDSM/Spansh calls).

Usage:
    python scripts/colonization_reachability_export.py \\
        --db data/sector_library/sector_eafots.sqlite \\
        --sector "Eafots" \\
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
from pathlib import Path
from typing import Optional

try:
    from scripts.extract_sector_systems_to_sqlite import sanitize_prefix
except ImportError:
    from extract_sector_systems_to_sqlite import sanitize_prefix


# ── Ring density reference (same constants as sector_survey.py) ────────────

GALACTIC_MEDIANS: dict[str, float] = {
    "Icy":      8.591068e-06,
    "Metallic": 9.251326e-06,
}

RING_BASE_POINTS: dict[str, float] = {
    "Metallic": 30.0,
    "Icy":      25.0,
}

# A ring only counts as "high-density" at or above this multiple of the
# galactic median surface density for its class.
RING_DENSITY_FLOOR = 1.0

SUBTYPE_ELW = "Earth-like world"
SUBTYPE_WW  = "Water world"
SUBTYPE_HMC = "High metal content world"
TERRAFORM_YES = "Terraformable"


def is_colonization_eligible(raw_json: Optional[str]) -> bool:
    """A system is only open for colonization if it has no established
    government -- i.e. systems.raw_json's `government` field is absent or
    the literal string "None". Anything else (Democracy, Feudal,
    Dictatorship, Cooperative, ...) means the system is already
    inhabited/claimed by a faction and is not a valid colonization target."""
    if not raw_json:
        return True
    try:
        gov = json.loads(raw_json).get("government")
    except (json.JSONDecodeError, TypeError):
        return True
    return gov is None or gov == "None"


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


def score_ring(ratio: float, ring_class: str) -> float:
    base = RING_BASE_POINTS[ring_class]
    return base * (1.0 + (ratio - 1.0) * 0.5)


# ── Reachability graph ───────────────────────────────────────────────────

def build_adjacency(
    coords: list[tuple[float, float, float]],
    max_jump_ly: float,
) -> dict[int, list[int]]:
    """Grid-bucketed spatial adjacency: edge between any two points within
    max_jump_ly of each other. Cell size = max_jump_ly, 27-neighbor-cell
    candidate check keeps this near-linear for realistically sparse sectors."""
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


# ── Data extraction for the reachable set ───────────────────────────────

def score_systems(
    conn: sqlite3.Connection,
    reachable_keys: list[str],
) -> dict[str, dict]:
    """Body/ring aggregation + scoring, scoped to just the reachable systems."""
    placeholders = ",".join("?" for _ in reachable_keys)
    results: dict[str, dict] = {
        k: {
            "elw_count": 0, "ww_count": 0, "hmct_count": 0,
            "metallic_ratio": 0.0, "icy_ratio": 0.0, "total_body_count": 0,
        }
        for k in reachable_keys
    }

    for system_key, body_type, sub_type, terraform_state in conn.execute(
        f"SELECT system_key, body_type, sub_type, terraform_state FROM bodies "
        f"WHERE system_key IN ({placeholders})",
        reachable_keys,
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
        f"WHERE system_key IN ({placeholders}) AND raw_json IS NOT NULL",
        reachable_keys,
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
    placeholders = ",".join("?" for _ in reachable_keys)
    details: dict[str, dict] = {
        k: {"stars": [], "planet_types": [], "rings": []}
        for k in reachable_keys
    }

    for system_key, sub_type, raw_json in conn.execute(
        f"SELECT system_key, sub_type, raw_json FROM bodies "
        f"WHERE system_key IN ({placeholders}) AND body_type = 'Star'",
        reachable_keys,
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
        f"WHERE system_key IN ({placeholders}) AND body_type = 'Planet'",
        reachable_keys,
    ):
        d = details.get(system_key)
        if d is None:
            continue
        d["planet_types"].append(sub_type or "Unknown")

    for system_key, ring_name, raw_json in conn.execute(
        f"SELECT system_key, ring_name, raw_json FROM rings "
        f"WHERE system_key IN ({placeholders}) AND raw_json IS NOT NULL",
        reachable_keys,
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
    db_path: Path,
    origin_system: str,
    max_jump_ly: float,
    max_hops: int,
    out_dir: Path,
    sector_label: str,
    dense_top_n: int = 20,
) -> tuple[Path, Path]:
    print("Colonization Reachability Export")
    print(f"  Source DB:     {db_path}")
    print(f"  Origin system: {origin_system}")
    print(f"  Max jump:      {max_jump_ly} ly/hop")
    print(f"  Max hops:      {max_hops}")
    print()

    conn = sqlite3.connect(str(db_path))

    print("Loading systems...")
    rows = conn.execute(
        "SELECT system_key, name, x, y, z, raw_json FROM systems WHERE x IS NOT NULL"
    ).fetchall()
    keys     = [r[0] for r in rows]
    names    = [r[1] for r in rows]
    coords   = [(r[2], r[3], r[4]) for r in rows]
    eligible = [is_colonization_eligible(r[5]) for r in rows]
    n = len(rows)
    print(f"  {n:,} systems loaded.")

    name_to_idx = {name: i for i, name in enumerate(names)}
    origin_idx = name_to_idx.get(origin_system)
    if origin_idx is None:
        conn.close()
        raise ValueError(f"Origin system {origin_system!r} not found in {db_path}")

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
    # systems are valid colonization targets -- occupied systems are
    # excluded from both output rankings below.
    eligible_idxs = [i for i in reachable_idxs if eligible[i]]
    excluded_count = len(reachable_idxs) - len(eligible_idxs)
    print(f"  {len(eligible_idxs):,} of those are colonization-eligible "
          f"(ungoverned); {excluded_count:,} excluded as already inhabited/claimed.")

    print("Scoring reachable systems (bodies + rings)...")
    scored = score_systems(conn, reachable_keys)

    print("Extracting stellar/planetary composition for densest-systems report...")
    dense_details = extract_dense_system_details(conn, reachable_keys)
    conn.close()

    origin_coords = coords[origin_idx]
    out_rows = []
    for i in eligible_idxs:
        key = keys[i]
        name = names[i]
        x, y, z = coords[i]
        s = scored[key]

        elw_score  = score_tiered(s["elw_count"], 2, 100.0, 50.0)
        ww_score   = score_tiered(s["ww_count"], 2, 40.0, 20.0)
        hmct_score = score_tiered(s["hmct_count"], 3, 30.0, 15.0)

        metallic_score = score_ring(s["metallic_ratio"], "Metallic") if s["metallic_ratio"] > 0 else 0.0
        icy_score      = score_ring(s["icy_ratio"], "Icy") if s["icy_ratio"] > 0 else 0.0
        ring_score = metallic_score + icy_score

        total = elw_score + ww_score + hmct_score + ring_score

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
        "colonization_score",
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
    parser.add_argument("--db", type=Path, required=True, help="Path to sector library SQLite file")
    parser.add_argument("--sector", required=True, help="Sector name (for labeling/output filename)")
    parser.add_argument("--origin-system", required=True, help="Origin system name (must exist in the DB)")
    parser.add_argument("--max-jump-ly", type=float, default=15.0, help="Max ly per hop (default: 15)")
    parser.add_argument("--max-hops", type=int, default=15, help="Max chain length in hops (default: 15)")
    parser.add_argument("--dense-top-n", type=int, default=20,
                         help="Number of systems in the densest-systems (planetary body count) report (default: 20)")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Output directory (default: out)")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 1

    try:
        run(
            db_path=args.db,
            origin_system=args.origin_system,
            max_jump_ly=args.max_jump_ly,
            max_hops=args.max_hops,
            out_dir=args.out_dir,
            sector_label=args.sector,
            dense_top_n=args.dense_top_n,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
