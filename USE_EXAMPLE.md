# Use Example: Scouting a Colony Site Near the Galtean Bubble

This walks through a full, realistic session with ED Colony Scout, start to
finish — extracting a sector, ranking candidates, picking one, and charting
the route to it. Every number below comes from actually running the tool
against a real Spansh extraction; nothing here is simulated.

## Scenario

Project Galtea, a player-run colonization initiative, intends to found a new
bubble of human systems out in the Eotchorts sector — far from the existing
bubble. Galtea has publicly named one system as a reference point "close to"
their (unannounced) capital system: **Eotchorts EG-X d1-265**.

CMDR wants to get ahead of the rush: identify and evaluate every
colonization-eligible system within **10 hops** (at a **15 ly** max jump) of
that reference system now, so that once the jump-chain of colonies actually
reaches this far out, there's already a ranked shortlist — and a known route
to whichever one gets picked.

## Step 1 — Get the source data

Downloaded a full **all-systems** galaxy dump from
[Spansh](https://spansh.co.uk/dumps) (`galaxy.json.gz`) — not the
"populated systems only" dump, since the reachability graph needs
uninhabited systems too as hop-through waypoints.

## Step 2 — Prep Sectors

On the **Prep Sectors** tab:

- **Galaxy dump path**: pointed at the downloaded `galaxy.json.gz`.
- **Sector library directory**: `data/sector_library` (default).
- **Sector Name Prefixes**: added `Eotchorts`.
- Clicked **Extract**.

This streams the dump once and writes `sector_eotchorts.sqlite` into the
sector library directory. For this run, extraction pulled in:

- **7,391 systems**
- **22,341 bodies**

Equivalent CLI form:

```bash
python scripts/extract_multi_sector_to_sqlite.py \
    --input "/path/to/galaxy.json.gz" \
    --sector_prefix "Eotchorts"
```

## Step 3 — Run

On the **Run** tab:

- **Sector Database(s)**: added `data/sector_library/sector_eotchorts.sqlite`.
- **Sector label**: `Eotchorts`.
- **Origin System → System name**: `Eotchorts EG-X d1-265`.
- **Max jump (ly)**: `15` (left at default).
- **Max hops**: `10`.
- **Densest-systems top N**: `20`.
- Scoring rubric: left unchanged (stock values).
- Clicked **Run**.

Log output:

```
Building spatial adjacency graph...
  4,122 edges (<= 15.0 ly apart).
Running BFS from origin (cap 10 hops)...
  340 systems reachable within 10 hops.
  340 of those are colonization-eligible (ungoverned); 0 excluded as already inhabited/claimed.
```

Every system within range came back ungoverned — unsurprising this far
outside the existing bubble.

Equivalent CLI form:

```bash
python scripts/colonization_reachability_export.py \
    --db data/sector_library/sector_eotchorts.sqlite \
    --sector "Eotchorts" \
    --origin-system "Eotchorts EG-X d1-265" \
    --max-jump-ly 15 --max-hops 10 --dense-top-n 20 --out-dir out
```

## Step 4 — Review the Candidates tab

The **Candidates** table (and the matching
[Eotchorts_Eotchorts_EG-X_d1-265_colonization_candidates.csv](out/Eotchorts_Eotchorts_EG-X_d1-265_colonization_candidates.csv))
ranked all 339 reachable, colonization-eligible systems by score. Top of the list:

| Rank | System | Score | Key factors | Hops |
|---|---|---|---|---|
| 1 | Eotchorts CL-X d1-183 | 312.0 | ELW×1, WW×2, HMC-T×4 | 9 |
| 2 | Eotchorts EG-X d1-270 | 240.0 | ELW×1, WW×2, HMC-T×2 | 9 |
| 3 | Eotchorts NS-S c4-20 | 240.0 | ELW×2, WW×1 | 2 |
| 4 | Eotchorts LX-S c4-31 | 220.0 | ELW×1, HMC-T×5 | 1 |
| 5 | Eotchorts EG-X d1-278 | 190.0 | ELW×1, HMC-T×3 | 8 |

Scrolling further down the Candidates table, CMDR's eye lands on:

| Rank | System | Score | Key factors | Hops | Straight-line distance from origin |
|---|---|---|---|---|---|
| 15 | Eotchorts FG-X d1-435 | 130.0 | ELW×1, HMC-T×1 | 10 | 92.65 ly |

Not the top-scoring system in range, but a solid 1 ELW + 1 terraformable HMC
world at the outer edge of the 10-hop search radius — CMDR decides this is
the one to claim.

The **Densest Systems** tab/CSV was also populated (top 20 by planetary body
count, e.g. Eotchorts LX-S c4-3 with 44 planets at 3 hops) as a secondary
reference, but wasn't the deciding factor here — see
[Eotchorts_Eotchorts_EG-X_d1-265_densest_systems.csv](out/Eotchorts_Eotchorts_EG-X_d1-265_densest_systems.csv)
for the full list.

## Step 5 — Path Finder: Shortest Path

With `Eotchorts FG-X d1-435` picked as the colonization target, switched to
the **Path Finder** tab (still using the same sector DB selection from the
Run tab):

- Mode: **Shortest Path**.
- **Origin system**: `Eotchorts EG-X d1-265`.
- **Target system**: `Eotchorts FG-X d1-435`.
- **Max jump (ly)**: `15` (left at default).
- Clicked **Find Path**.

Result: **10 hops**, matching the hop count already shown on the Candidates
table. Full route (also written to
[Eotchorts_EG-X_d1-265_to_Eotchorts_FG-X_d1-435_shortest_path.csv](out/Eotchorts_EG-X_d1-265_to_Eotchorts_FG-X_d1-435_shortest_path.csv)):

| Hop | System | Leg distance (ly) |
|---|---|---|
| 0 | Eotchorts EG-X d1-265 (origin) | — |
| 1 | Eotchorts LX-S c4-23 | 13.47 |
| 2 | Eotchorts FG-X d1-42 | 14.88 |
| 3 | Eotchorts FG-X d1-236 | 14.40 |
| 4 | Eotchorts IR-U c3-3 | 12.16 |
| 5 | Eotchorts FG-X d1-353 | 8.72 |
| 6 | Eotchorts FG-X d1-140 | 10.40 |
| 7 | Eotchorts FG-X d1-395 | 13.45 |
| 8 | Eotchorts FG-X d1-129 | 13.75 |
| 9 | Eotchorts FG-X d1-15 | 11.53 |
| 10 | Eotchorts FG-X d1-435 (target) | 10.50 |

Every leg is comfortably under the 15 ly cap, and none of the waypoints are
occupied — all ten are open hops.

Equivalent CLI form:

```bash
python scripts/path_finder_export.py \
    --db data/sector_library/sector_eotchorts.sqlite \
    shortest-path --origin-system "Eotchorts EG-X d1-265" \
    --target-system "Eotchorts FG-X d1-435" --max-jump-ly 15 --out-dir out
```

## Outcome

CMDR now has:

- A ranked shortlist of 339 colonization-eligible systems within 10 hops of
  the public Galtea reference point, ready before the in-game jump-chain
  even arrives.
- A specific target, **Eotchorts FG-X d1-435**, picked for its ELW + HMC-T
  combination.
- A confirmed, jump-by-jump bridging route from the reference system to that
  target — 10 hops, all under 15 ly, all open — ready to fly (or hand off to
  whoever is laying the actual colony chain) the moment it's needed.

All of the above was produced entirely offline from a local galaxy dump; no
part of this workflow makes a network call.

## Output files

The CSVs this walkthrough produced are included in the repo under `out/` as
worked examples:

- [Eotchorts_Eotchorts_EG-X_d1-265_colonization_candidates.csv](out/Eotchorts_Eotchorts_EG-X_d1-265_colonization_candidates.csv) — full ranked Candidates list (Step 4).
- [Eotchorts_Eotchorts_EG-X_d1-265_densest_systems.csv](out/Eotchorts_Eotchorts_EG-X_d1-265_densest_systems.csv) — full Densest Systems list (Step 4).
- [Eotchorts_EG-X_d1-265_to_Eotchorts_FG-X_d1-435_shortest_path.csv](out/Eotchorts_EG-X_d1-265_to_Eotchorts_FG-X_d1-435_shortest_path.csv) — full hop-by-hop route (Step 5).
