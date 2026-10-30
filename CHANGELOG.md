# Changelog

All notable changes to this project are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/);
this project uses [Semantic Versioning](https://semver.org/).

> **Note on early history**: only `v0.3.0` exists as an actual git tag.
> The `0.1.0` and `0.2.0` entries below are inferred from commit history and
> `gui/config.py`'s migration comment ("v0.1.0 used a single `db_path`
> string") rather than independently verified against a tag -- flagged here
> so that inference isn't mistaken for a verified release record.

## [1.1.0] - Unreleased

Reliability fixes from an engineering review of the Zurara Bridge research
tooling. Results on clean data are unchanged, except that systems with
unreadable stored data no longer count as colonization-eligible.

### Changed
- **Eligibility has three states.** A system with missing, malformed or
  non-object stored data is now *unknown*, not eligible: it's excluded from
  the Candidates and Densest Systems rankings (the log reports how many) and
  isn't treated as an occupied system by the reachability check. A
  well-formed record with no government field is still eligible.
- The status bar describes how the last run ended (completed, failed,
  completed but skipped unreadable records, cancelled) instead of showing a
  bare exit code.

### Fixed
- A job that hit an unexpected error reported success (exit 0); it now
  reports failure (exit 1), and results from a failed run aren't loaded.
- Extraction: a failed final write returned success and could leave the
  database open; it now fails, closes the connection, and reports which
  sector failed (other sectors are still written).
- Extraction: unreadable lines in the galaxy dump were skipped silently.
  They're now counted and listed in the log, and the run exits with code 3
  (its databases are still offered to the Run tab). `--strict` stops at the
  first one instead.
- Extraction: a new sector database is built as `.partial` and renamed only
  when the run completes, so a cancelled or failed extraction no longer
  leaves a half-written database that looks complete.
- Extraction: `--limit 1` extracted nothing in the single-sector script;
  `--limit N` now means "scan N records" in both scripts. The multi-sector
  script now honors its body and ring commit thresholds.
- Extraction: systems with a missing or invalid coordinate are stored with
  empty coordinates (and counted) instead of a partial position that crashed
  the reachability graph. The loader also leaves out such rows from older
  databases.
- Extraction: the `ring_class`, `mass_mt`, `inner_rad` and `outer_rad`
  columns were always empty because the extractor looked for the wrong
  Spansh keys. New extractions fill them; older databases keep working
  because the app reads ring details from `raw_json`.
- Scoring a very large reachable set failed with "too many SQL variables";
  scoring and the densest-systems report now stage the system list in a
  temporary table.
- Sector databases are opened read-only for analysis, and a missing database
  path is reported instead of silently creating an empty file.
- A zero, negative, infinite or non-numeric jump range, or a negative or
  fractional hop cap, is now rejected with a clear error.

## [1.0.0] - 2026-08-07

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
- Per-user Inno Setup installer (`packaging/installer.iss`,
  `ED-Colony-Scout-Setup-X.Y.Z.exe`) with upgrade-safe hygiene (closes a
  running app before installing, wipes the prior bundle directory so stale
  files can't accumulate across upgrades) and a matching "Antivirus and
  SmartScreen warnings" README section covering checksum verification and
  false-positive recovery, since the installer isn't code-signed.
- Automated test suite (`tests/`, stdlib `unittest`) covering extraction,
  scoring, reachability, path-finding, and the System Detail data layer
  against a small fixture sector built through the real extraction
  pipeline -- no automated tests existed before this release.
- GitHub Actions CI (`.github/workflows/ci.yml`, runs the test suite on
  every push/PR) and a release workflow
  (`.github/workflows/release.yml`, builds and publishes the packaged exe
  to a GitHub Release on `v*` tag push, gated on tests passing).

### Security
- The actual `v1.0.0` GitHub Release asset (built by `release.yml`, not a
  local dev build) was scanned on VirusTotal:
  [full report](https://www.virustotal.com/gui/file/7bfac9513ffbf397ed376e5100b68ba435ce23c37cee7a9c5624d89b7074133d).
  7 of 71 engines flagged it, all with generic obfuscation/ML-heuristic
  labels (`trojan.obfuscatedpoly`), not an identified malware family.
  Notably Microsoft/Windows Defender is among them
  (`Trojan:Win32/Wacatac.B!ml`, a cloud ML classifier flag, not a signature
  match) — a commonly reported false-positive label for unsigned freshly-
  built PyInstaller executables, but real enough that some users on this
  release may see an actual Defender warning. A local dev build of the
  identical source scanned cleaner (5/71, no Defender flag), confirming
  PyInstaller builds aren't byte-reproducible between machines — scan the
  actual release asset, not a dev build. See the README's
  [Antivirus and SmartScreen warnings](README.md#antivirus-and-smartscreen-warnings)
  section for full detail.
- SHA-256 (`ED-Colony-Scout-Setup-1.0.0.exe`, the actual release asset):
  `7BFAC9513FFBF397ED376E5100B68BA435CE23C37CEE7A9C5624D89B7074133D`

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
