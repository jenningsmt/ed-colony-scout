"""Shared fixture-sector builder for the test suite.

`*.sqlite` is gitignored (extracted sector DBs are large, user-generated
data), so there's no binary fixture DB to commit. Instead, tests build a
tiny fixture sector DB at setup time from a small Spansh-shaped JSON
fixture, run through the real extraction pipeline
(scripts.extract_sector_systems_to_sqlite.run()) -- exercising real
extraction code rather than a static blob.

Layout is a short linear chain along the x-axis, distances chosen against a
15 ly max jump (this project's default):

    ORIGIN --10ly-- RICH --10ly-- FAR --10ly-- OCCUPIED      ISOLATED
    (0,0,0)         (10,0,0)      (20,0,0)     (30,0,0)      (1000,0,0)

- ORIGIN: minimal, unoccupied, the reachability/path-finder origin.
- RICH: 2 Earth-like worlds, 1 water world, 1 terraformable + 1
  non-terraformable HMC world (only the terraformable one should score),
  a biosignal body (with genuses) and a geosignal body, an Icy ring (using
  the exact type/mass/innerRadius/outerRadius shape real Spansh dumps use,
  which is NOT what extract_sector_systems_to_sqlite's flattened ring
  columns look for -- see the known key-casing mismatch documented in
  gui/db_lookup.py), and a Barycentre row (excluded from total_body_count).
- FAR: 20ly from ORIGIN (no direct edge at 15ly/hop) but 10ly from RICH, so
  only reachable via a 2-hop chain through RICH.
- OCCUPIED: has an established government, so it's a valid hop-through
  waypoint but must never appear in colonization-candidate output.
- ISOLATED: 970ly from its nearest neighbor (OCCUPIED) -- disconnected at
  any realistic max_jump_ly, exercising "not reachable"/"not connected"
  paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import extract_sector_systems_to_sqlite as extractor

SECTOR_PREFIX = "TestSector"

ORIGIN = "TestSector AA-A a1"
RICH = "TestSector AA-A a2"
FAR = "TestSector AA-A a3"
OCCUPIED = "TestSector AA-A a4"
ISOLATED = "TestSector AA-A a5"

ICY_RING = {
    "name": f"{RICH} 7 A Ring",
    "type": "Icy",
    "mass": 228600000000.0,
    "innerRadius": 61071000.0,
    "outerRadius": 105120000.0,
}

BIO_SIGNALS = {
    "signals": {"$SAA_SignalType_Biological;": 7},
    "genuses": ["$Codex_Ent_Shrubs_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;"],
}
GEO_SIGNALS = {"signals": {"$SAA_SignalType_Geological;": 3}}


def _star(name: str, body_id: int) -> dict:
    return {
        "name": name,
        "bodyId": body_id,
        "type": "Star",
        "subType": "G (White-Yellow) Star",
        "mainStar": True,
        "distanceToArrival": 0.0,
    }


def _planet(
    name: str,
    body_id: int,
    sub_type: str,
    terraform_state: str = "Not terraformable",
    is_landable: bool = False,
    rings: list[dict] | None = None,
    signals: dict | None = None,
) -> dict:
    body = {
        "name": name,
        "bodyId": body_id,
        "type": "Planet",
        "subType": sub_type,
        "terraformingState": terraform_state,
        "isLandable": is_landable,
    }
    if rings:
        body["rings"] = rings
    if signals:
        body["signals"] = signals
    return body


def _barycentre(name: str, body_id: int) -> dict:
    return {"name": name, "bodyId": body_id, "type": "Barycentre"}


def _system(name: str, id64: int, x: float, y: float, z: float, bodies: list[dict], government=None) -> dict:
    system = {
        "name": name,
        "id64": id64,
        "coords": {"x": x, "y": y, "z": z},
        "bodies": bodies,
    }
    if government is not None:
        system["government"] = government
    return system


FIXTURE_SYSTEMS = [
    _system(
        ORIGIN, 1_000_000_000_001, 0.0, 0.0, 0.0,
        [
            _star(f"{ORIGIN} A", 0),
            _planet(f"{ORIGIN} 1", 1, "Metal-rich body", is_landable=True),
        ],
    ),
    _system(
        RICH, 1_000_000_000_002, 10.0, 0.0, 0.0,
        [
            _star(f"{RICH} A", 0),
            _planet(f"{RICH} 1", 1, "Earth-like world"),
            _planet(f"{RICH} 2", 2, "Earth-like world"),
            _planet(f"{RICH} 3", 3, "Water world", terraform_state="Terraformable"),
            _planet(f"{RICH} 4", 4, "High metal content world", terraform_state="Terraformable", is_landable=True),
            _planet(f"{RICH} 5", 5, "High metal content world", terraform_state="Not terraformable", is_landable=True),
            _planet(f"{RICH} 6", 6, "Metal-rich body", is_landable=True, signals=BIO_SIGNALS),
            _planet(f"{RICH} 7", 7, "Icy body", rings=[ICY_RING]),
            _planet(f"{RICH} 8", 8, "Rocky body", signals=GEO_SIGNALS),
            _barycentre(f"{RICH} Barycentre 1", 9),
        ],
    ),
    _system(
        FAR, 1_000_000_000_003, 20.0, 0.0, 0.0,
        [
            _star(f"{FAR} A", 0),
            _planet(f"{FAR} 1", 1, "Icy body"),
        ],
    ),
    _system(
        OCCUPIED, 1_000_000_000_004, 30.0, 0.0, 0.0,
        [
            _star(f"{OCCUPIED} A", 0),
            _planet(f"{OCCUPIED} 1", 1, "High metal content world", terraform_state="Terraformable"),
        ],
        government="Democracy",
    ),
    _system(
        ISOLATED, 1_000_000_000_005, 1000.0, 0.0, 0.0,
        [
            _star(f"{ISOLATED} A", 0),
            _planet(f"{ISOLATED} 1", 1, "Rocky body"),
        ],
        government="None",  # exercises the literal-string-"None" branch of is_colonization_eligible
    ),
]


def write_fixture_jsonl(path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for system in FIXTURE_SYSTEMS:
            f.write(json.dumps(system) + "\n")


def build_fixture_db(tmp_dir: Path) -> Path:
    """Writes the fixture JSONL and runs it through the real extraction
    pipeline, returning the resulting sqlite path."""
    jsonl_path = tmp_dir / "fixture_galaxy.jsonl"
    write_fixture_jsonl(jsonl_path)
    db_path = tmp_dir / "sector_testsector.sqlite"
    rc = extractor.run(
        input_path=jsonl_path,
        sector_prefix=SECTOR_PREFIX,
        output_db=db_path,
    )
    if rc != 0:
        raise RuntimeError(f"fixture extraction failed with code {rc}")
    return db_path
