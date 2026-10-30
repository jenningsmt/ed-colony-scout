"""Catalog loading and scoring safeguards added in v1.1.0: read-only loading,
eligibility status, position validation, and large reachable sets."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import colonization_reachability_export as reach
from scripts import extract_sector_systems_to_sqlite as extractor
from scripts import path_finder_export as path_finder


def _make_db(path: Path, systems: list[dict]) -> None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        extractor.init_db(conn)
        rows, bodies = [], []
        for system in systems:
            row, body_rows, _ = extractor.process_system(system, "Test", True)
            rows.append(row)
            bodies.extend(body_rows)
        extractor.upsert_systems(conn, rows)
        extractor.upsert_bodies(conn, bodies)
        conn.commit()


def _system(name, address, x=0.0, government=None, bodies=()):
    data = {"name": name, "id64": address, "coords": {"x": x, "y": 0, "z": 0},
            "bodies": [{"name": f"{name} {i}", "bodyId": i, "type": "Planet", "subType": sub}
                       for i, sub in enumerate(bodies, start=1)]}
    if government is not None:
        data["government"] = government
    return data


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, *paths):
        with contextlib.redirect_stdout(io.StringIO()):
            return reach.load_merged_systems(list(paths))

    def test_missing_database_raises_and_creates_nothing(self):
        missing = self.tmp / "nope.sqlite"
        with self.assertRaises(FileNotFoundError):
            self.load(missing)
        self.assertFalse(missing.exists())

    def test_status_is_tracked_per_system(self):
        db = self.tmp / "s.sqlite"
        _make_db(db, [_system("Test AA-A a0", 1), _system("Test AA-A a1", 2, 5.0, "Democracy")])
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("UPDATE systems SET raw_json = 'not json' WHERE name = 'Test AA-A a1'")
            conn.execute("INSERT INTO systems (system_key, name, x, y, z, raw_json) VALUES "
                         "('addr:3', 'Test AA-A a2', 9, 0, 0, ?)", (json.dumps({"government": "Feudal"}),))
            conn.commit()
        merged = self.load(db)
        status = dict(zip(merged.names, merged.status))
        self.assertEqual(status, {"Test AA-A a0": reach.ELIGIBLE, "Test AA-A a1": reach.UNKNOWN,
                                  "Test AA-A a2": reach.INELIGIBLE})
        self.assertEqual(merged.eligible, [s == reach.ELIGIBLE for s in merged.status])

    def test_rows_without_a_valid_position_are_left_out(self):
        db = self.tmp / "s.sqlite"
        _make_db(db, [_system("Test AA-A a0", 1)])
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute("INSERT INTO systems (system_key, name, x, y, z) VALUES ('addr:2', 'Test AA-A a1', 1, NULL, 0)")
            conn.execute("INSERT INTO systems (system_key, name, x, y, z) VALUES ('addr:3', 'Test AA-A a2', 'abc', 0, 0)")
            conn.commit()
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            merged = reach.load_merged_systems([db])
        self.assertEqual(merged.names, ["Test AA-A a0"])
        self.assertIn("2 without a valid position", log.getvalue())

    def test_unknown_status_is_not_treated_as_occupied(self):
        coords = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        status = [reach.ELIGIBLE, reach.UNKNOWN, reach.INELIGIBLE]
        occupied = [s == reach.INELIGIBLE for s in status]
        self.assertEqual(path_finder._nearest_occupied(0, coords, occupied, 5), [2])


class ScoringScaleTests(unittest.TestCase):
    def test_scores_are_correct_beyond_the_variable_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "s.sqlite"
            _make_db(db, [
                _system("Test AA-A a0", 1, bodies=["Earth-like world", "Water world"]),
                _system("Test AA-A a1", 2, 5.0, bodies=["Water world"]),
            ])
            conn = reach.connect_read_only(db)
            try:
                conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 10)
                padding = [f"addr:{n}" for n in range(1000, 1500)]
                keys = ["addr:1", "addr:2", "addr:1"] + padding  # duplicates and absent keys
                scored = reach.score_systems(conn, keys)
                details = reach.extract_dense_system_details(conn, keys)
            finally:
                conn.close()
        self.assertEqual((scored["addr:1"]["elw_count"], scored["addr:1"]["ww_count"]), (1, 1))
        self.assertEqual(scored["addr:2"]["ww_count"], 1)
        self.assertEqual(scored["addr:1000"]["total_body_count"], 0)
        self.assertEqual(details["addr:1"]["planetary_body_count"], 2)
        self.assertEqual(details["addr:1000"]["planetary_body_count"], 0)


class HopValidationTests(unittest.TestCase):
    def test_negative_or_fractional_hops_are_rejected(self):
        adjacency = reach.build_adjacency([(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)], 15.0)
        for bad in (-1, 1.5, True):
            with self.subTest(max_hops=bad):
                with self.assertRaises(ValueError):
                    reach.bfs_hop_distances(adjacency, 0, bad)
                with self.assertRaises(ValueError):
                    path_finder.bfs_with_parents(adjacency, 0, bad)
        self.assertEqual(reach.bfs_hop_distances(adjacency, 0, 0), {0: 0})


if __name__ == "__main__":
    unittest.main()
