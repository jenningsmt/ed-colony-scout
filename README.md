# ED Colony Scout

Colonization-planning tooling for Elite Dangerous: given an already-extracted
sector of the galaxy, rank nearby systems by colonization value, chart the
actual jump-chain path between two systems, and check whether a target
system can be reached — and claimed — from an already-inhabited launch
point within a given number of hops.

Everything runs entirely offline against a local SQLite extraction of a
[Spansh](https://spansh.co.uk/dumps) galaxy dump. No network calls of any
kind — see [Data privacy](#data-privacy) below.

![ED Colony Scout — Prep Sectors tab](docs/images/screenshot.jpg)

## What it does

- **Prep Sectors** — extract one or more named sectors from a full Spansh
  galaxy dump into local SQLite databases, in a single pass over the dump.
- **Run** — pick one or more extracted sector databases and an origin system
  (typically your own colony), and rank every reachable, colonization-eligible
  system by a transparent, user-editable scoring rubric.
- **Path Finder** — two focused queries built on the same reachability graph:
  - **Shortest Path**: the true shortest hop-chain route between any two
    named systems.
  - **Reachable?**: given a target system, checks the nearest
    already-occupied systems and reports whether (and from where, and in how
    many hops) a colony claim on that target is actually launchable.
- **Candidates** / **Densest Systems** — sortable result tables: colonization
  candidates ranked by score, and the reachable systems with the most
  planetary bodies.

## Installation (running from source)

There's no packaged installer yet — run it from a Python checkout:

```bash
git clone <this repo>
cd ed-colony-scout
pip install -r requirements.txt
python -m gui.main
```

Requires **Python 3.10+** (the codebase uses `from __future__ import
annotations` and PEP 604-style unions throughout).

## Getting started

![ED Colony Scout — Run tab](docs/images/run-tab.png)

1. **Prep Sectors tab** — set a galaxy dump path (a full Spansh
   `galaxy.json.gz` — the **all-systems** dump, not the "populated systems
   only" one; see [Source data](#source-data-the-galaxy-dump)) and a sector
   library directory, add one or more sector name prefixes (e.g.
   `Parrots Head Sector`), and hit **Extract**. This streams the dump once
   and writes one SQLite database per sector.
2. **Run tab** — add the extracted sector database(s) you want included,
   pick an origin system (normally your existing colony), set the jump/hop
   parameters, and hit **Run**. Results land in the Candidates and Densest
   Systems tabs.
3. **Path Finder tab** — once you've spotted a promising candidate, use
   **Shortest Path** to chart the actual route to it, or **Reachable?** to
   check whether it can be claimed at all given where the nearest inhabited
   systems actually are.

Selecting **multiple** sector databases on the Run tab merges them into one
reachability graph before anything else runs — routing through an adjacent
sector's systems can shorten a hop chain or make an otherwise-unreachable
candidate reachable, and this is the only way to see that. The Path Finder
tab reuses whichever databases are currently selected on the Run tab, rather
than picking them again.

## Reachability model

"Reachable within N hops" means a *chain* of jumps, each within
**Max Jump (ly)** of the *previous* system in the chain — not a single
radius drawn around the origin. This mirrors how colonization bridging
actually works in-game: a sequence of short hops can reach much farther than
any single jump. Uninhabited systems are valid waypoints in that chain; they
just aren't valid *colonization targets* themselves unless they're also
eligible (see below).

Internally this is a grid-bucketed spatial adjacency graph (cell size = max
jump distance, checking the 27 neighboring cells of each system) followed by
a breadth-first search from the origin, capped at **Max Hops**. The graph is
undirected, which the Path Finder tab's **Reachable?** mode exploits: rather
than running a separate search per candidate launch point, it runs one
search rooted at the target and reads every candidate's hop distance from
that single result.

## Colonization eligibility

A system is a valid colonization target only if it has **no established
government** — i.e. it isn't already claimed by a faction. This is a fixed
game-mechanic rule, not a scoring preference, and it isn't editable. Occupied
systems still function as hop-through waypoints when building the
reachability graph (you can route *through* an inhabited system on the way
to somewhere else); they're simply excluded from the ranked Candidates and
Densest Systems output, since you can't found a new colony there.

## Scoring

Every reachable, colonization-eligible system earns points across five
categories; **Score** is the sum. The default ("stock") point values:

| Category | First tier | Beyond first tier |
|---|---|---|
| Earth-like World | first 2 @ 100 pts each | 50 pts each |
| Water World | first 2 @ 40 pts each | 20 pts each |
| Terraformable High Metal Content World | first 3 @ 30 pts each | 15 pts each |
| Metallic ring (see below) | 30 base pts | scaled by density |
| Icy ring (see below) | 25 base pts | scaled by density |

**Rings**: only a system's single densest ring *per class* counts, and only
if its surface density (mass ÷ ring cross-sectional area) is at or above the
galactic median for that class — 8.591068 × 10⁻⁶ kg/m² for Icy,
9.251326 × 10⁻⁶ kg/m² for Metallic. A qualifying ring's score is
`base_points × (1 + (density_ratio − 1) × 0.5)`, so denser rings score more
within the same base.

**None of this is a black box.** Every point value and tier size above (11
fields total: three tier sizes and eight point values) is editable via the
Run tab's **Customize Scoring...** dialog, with a **?** button next to it
that always shows the exact values currently in effect. The ring density
floor, the ring bonus curve's `0.5` slope, and the galactic median density
constants stay fixed — they're properties of how the median itself is
computed, not preferences — and the occupied-system exclusion rule (above)
is likewise fixed and documented in the same help text rather than a silent
filter. The **Candidates** table's **Key Scoring Factors** column names
which categories actually contributed to each system's score (e.g.
`ELW+WW+Icy`), ordered by point contribution.

![ED Colony Scout — Candidates tab](docs/images/candidates-tab.png)

## Path Finder

Both modes on this tab operate on whichever sector database(s) are currently
selected on the Run tab.

**Shortest Path** — enter an origin and a target system name; reports the
hop count and the full system-by-system route (including any uninhabited
waypoints), via an unbounded breadth-first search. A hop cap doesn't apply
here: a shortest path either exists in the loaded, connected data or it
doesn't.

**Reachable?** — enter a target system, a hop cap (or check **Any number of
hops**), and how many nearby occupied systems to check (default 10). Since
claiming a new colony requires launching from an *already-inhabited* system
within jump range — not necessarily your own colony — this finds the
nearest occupied systems to the target by straight-line distance, then
determines which of them (if any) reaches it within the hop cap, reporting
whichever one does so in the fewest hops. If none qualify, it reports the
**closest miss**: the nearest-by-hops candidate anyway, and by how many hops
it falls short, rather than a bare "not reachable."

Both modes also write their results to CSV in the configured output
directory (Settings tab), in addition to populating the tables in the GUI.

![ED Colony Scout — Path Finder tab, Reachable? mode](docs/images/path-finder-reachable-tab.png)

## Source data: the galaxy dump

Nothing in this repo ships with system data. You need a local copy of a full
[Spansh](https://spansh.co.uk/dumps) galaxy dump — the **all-systems** dump,
not "populated systems only"; the reachability graph needs uninhabited
systems too, since they're valid hop-through waypoints. This is a large file
(tens to over a hundred GB compressed); the Prep Sectors tab streams it once
regardless of how many sector prefixes you extract in that pass.

## Command-line usage

Every pipeline stage is also a standalone script, useful for scripting or
for exercising the underlying engine directly without the GUI:

```bash
# Extract one or more sectors from a galaxy dump in a single pass
python scripts/extract_multi_sector_to_sqlite.py \
    --input "/path/to/galaxy.json.gz" \
    --sector_prefix "Parrots Head Sector"

# Rank colonization candidates reachable from an origin (repeat --db to merge sectors)
python scripts/colonization_reachability_export.py \
    --db data/sector_library/sector_Parrots_Head_Sector.sqlite \
    --sector "Parrots Head Sector" \
    --origin-system "Parrot's Head Sector EL-Y d70" \
    --max-jump-ly 15 --max-hops 8 --out-dir out

# Shortest hop-chain path between two named systems
python scripts/path_finder_export.py \
    --db data/sector_library/sector_Parrots_Head_Sector.sqlite \
    shortest-path --origin-system "Parrot's Head Sector EL-Y d70" \
    --target-system "Parrot's Head Sector AF-A d35" --max-jump-ly 15 --out-dir out

# Is a target reachable from a nearby occupied system within a hop cap?
python scripts/path_finder_export.py \
    --db data/sector_library/sector_Parrots_Head_Sector.sqlite \
    reachable --target-system "Parrot's Head Sector AF-A d35" \
    --max-jump-ly 15 --max-hops 20 --candidates 10 --out-dir out
```

The CLI entry points always use the stock scoring rubric (customization is a
GUI-only feature at present) and write the same CSV outputs the GUI loads
into its tables.

## Dependencies

`pip install -r requirements.txt`.

- **Python 3.10+**.
- **`ijson`** — required by `scripts/extract_sector_systems_to_sqlite.py` to
  stream a Spansh galaxy dump that's a JSON-array document (rather than JSON
  Lines) without loading the whole file into memory. Performance depends
  heavily on backend — check with `python -c "import ijson; print(ijson.backend)"`;
  the compiled `yajl2_c` backend is dramatically faster than the pure-Python
  fallback.
- Everything else (`sqlite3`, `csv`, `json`, `argparse`, `tkinter`, `dataclasses`, ...)
  is standard library.

## Repository layout

```
scripts/
  extract_sector_systems_to_sqlite.py   Single-sector extraction from a galaxy dump
  extract_multi_sector_to_sqlite.py     Multi-sector extraction in one pass over the dump
  colonization_reachability_export.py   Reachability graph, scoring rubric, candidate ranking engine
  path_finder_export.py                 Shortest Path / Reachable? engine (reuses the above)

gui/
  app.py            Tkinter window: Prep Sectors, Run, Path Finder, Candidates, Densest Systems, Settings tabs
  worker.py         Background-thread job runner (stdout capture, cooperative cancellation)
  pipeline.py       Orchestrates scripts/ calls for the GUI, relays results back via a sentinel-line convention
  db_lookup.py      Read-only sqlite helpers backing the DB/origin/target pickers (type-ahead search, etc.)
  rubric.py         Scoring-rubric config<->dataclass round-trip, help text, and the customization dialog
  treeview_utils.py Generic sortable-Treeview + CSV/dict-loading helpers for the results tables
  config.py         Persisted settings (%APPDATA%\EDColonyScout\config.json)
  main.py           Entry point (`python -m gui.main`)

data/sector_library/   Extracted per-sector SQLite DBs (gitignored)
out/                   Generated CSV reports (gitignored)
```

## Data privacy

This program makes **no network calls of any kind**. Every pipeline stage —
extraction, scoring, reachability, path-finding — reads only from a local
galaxy dump file and locally-extracted SQLite databases you supply. Nothing
is ever sent anywhere.

## Caveats

- Extracted data is only as fresh as the galaxy dump it came from — Spansh
  dumps are periodic snapshots, and the actual game state (who's colonized
  what) changes continuously. Re-extract periodically if you're tracking a
  fast-moving region.
- Scoring is a planning aid, not a guarantee — it reflects the point values
  configured at run time, applied to whatever the source dump reported.
- The occupied-system exclusion relies on the dump correctly reporting a
  system's government/allegiance at the time it was captured; a system
  claimed very recently might not yet be reflected.

## Acknowledgments

This project exists only because of data and infrastructure built and
maintained by others, freely made available to the community:

- **[Spansh](https://spansh.co.uk/)**, created and maintained by **Gareth
  Harper** — the galaxy dumps this project's entire extraction pipeline is
  built on.
- **[EDDN](https://eddn.edcd.io/)** (Elite Dangerous Data Network) and the
  wider **[EDCD](https://github.com/EDCD)** community — the real-time,
  crowdsourced data network Spansh and the broader third-party tool
  ecosystem are ultimately built on top of.
- The Elite Dangerous commander community at large — every system this
  project's analysis is built on exists because a player chose to explore
  it and *voluntarily* submit that discovery through an EDDN-compatible
  tool.

## Legal

*Elite Dangerous* is a trademark and copyright of **Frontier Developments
plc**, all rights reserved. This is an unofficial, fan-made tool built by a
player of the game; it is not affiliated with, endorsed by, or sponsored by
Frontier Developments in any way. All Elite Dangerous game data, names, and
imagery referenced here are the property of Frontier Developments plc — see
their fan content policy (on their website) for the terms this project
operates under.
