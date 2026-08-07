# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/);
this project uses [Semantic Versioning](https://semver.org/).

> **Note on early history**: only `v0.3.0` exists as an actual git tag.
> The `0.1.0` and `0.2.0` entries below are inferred from commit history and
> `gui/config.py`'s migration comment ("v0.1.0 used a single `db_path`
> string") rather than independently verified against a tag -- flagged here
> so that inference isn't mistaken for a verified release record.

## [1.0.0] - 2026-08-06

First packaged release: a standalone Windows executable, an automated test
suite, and CI/release workflows, alongside the System Detail popup and
worked use-example added just prior.

### Added
- **System Detail popup**: double-click any system name in Candidates,
  Densest Systems, or either Path Finder result table to open a read-only
  breakdown of that system's stellar/planetary bodies (landable flag,
  terraform state, biosignal/geosignal counts with cleaned-up genus names)
  and rings.
- `USE_EXAMPLE.md`: a full worked walkthrough (Prep Sectors -> Run -> Path
  Finder) against a real sector extraction, with the actual output CSVs
  included in `out/` as reviewable examples.
- Packaged Windows executable via PyInstaller (`packaging/`): windowed
  onedir build, app icon (window + exe file), embedded version resource.
- Automated test suite (`tests/`, stdlib `unittest`) covering extraction,
  scoring, reachability, path-finding, and the System Detail data layer
  against a small fixture sector built through the real extraction
  pipeline -- no automated tests existed before this release.
- GitHub Actions CI (`.github/workflows/ci.yml`, runs the test suite on
  every push/PR) and a release workflow
  (`.github/workflows/release.yml`, builds and publishes the packaged exe
  to a GitHub Release on `v*` tag push, gated on tests passing).

### Fixed
- `gui/db_lookup.DEFAULT_SECTOR_LIBRARY_DIR` now resolves against the same
  `%LOCALAPPDATA%`-based path `gui/config.py` already uses, instead of a
  path relative to the source file that would point inside a PyInstaller
  temp/install bundle once frozen.

## [0.3.0] - 2026-08-06

- Added the **Path Finder** tab: **Shortest Path** (true shortest hop-chain
  route between two named systems) and **Reachable?** (finds the nearest
  already-occupied systems to a target and reports whether/how a colony
  claim on it is actually launchable within a hop cap).
- Added the README, with screenshots of each tab.

## [0.2.0] - 2026-08-06

- Added multi-sector Prep Sectors support (extract several sector prefixes
  from one pass over a galaxy dump).
- Added multi-DB reachability: selecting multiple sector databases on the
  Run tab merges them into one reachability graph, so routing through an
  adjacent sector can shorten a hop chain or make an otherwise-unreachable
  candidate reachable.
- Added the configurable, user-editable colonization scoring rubric
  ("Customize Scoring..." dialog).

## [0.1.0] - 2026-08-04

- Initial MVP: single-sector extraction from a Spansh galaxy dump, single
  origin/single-DB reachability ranking, Candidates and Densest Systems
  result tables.
