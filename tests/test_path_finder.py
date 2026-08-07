"""Tests for scripts/path_finder_export.py against the shared fixture
sector (tests/fixtures.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import path_finder_export as pf
from tests import fixtures


class PathFinderFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = fixtures.build_fixture_db(Path(self._tmp.name))

    def test_shortest_path_multi_hop_chain(self) -> None:
        result = pf.shortest_path(
            db_paths=[self.db_path],
            origin_system=fixtures.ORIGIN,
            target_system=fixtures.FAR,
            max_jump_ly=15.0,
            out_dir=None,
        )
        self.assertTrue(result["reachable"])
        self.assertEqual(result["hop_count"], 2)
        names = [row["system_name"] for row in result["path"]]
        self.assertEqual(names, [fixtures.ORIGIN, fixtures.RICH, fixtures.FAR])
        self.assertFalse(result["path"][1]["occupied"])

    def test_shortest_path_unreachable_target(self) -> None:
        result = pf.shortest_path(
            db_paths=[self.db_path],
            origin_system=fixtures.ORIGIN,
            target_system=fixtures.ISOLATED,
            max_jump_ly=15.0,
            out_dir=None,
        )
        self.assertFalse(result["reachable"])
        self.assertIsNone(result["hop_count"])
        self.assertEqual(result["path"], [])

    def test_check_reachable_finds_occupied_launch_point(self) -> None:
        result = pf.check_reachable(
            db_paths=[self.db_path],
            target_system=fixtures.FAR,
            max_jump_ly=15.0,
            max_hops=None,
            candidate_count=10,
            out_dir=None,
        )
        self.assertTrue(result["reachable"])
        self.assertEqual(len(result["candidates"]), 1)  # only one occupied system in the fixture
        self.assertEqual(result["best_candidate"]["system_name"], fixtures.OCCUPIED)
        self.assertEqual(result["best_candidate"]["hop_count"], 1)
        # Path is launch-point-first: the occupied system, then the target.
        names = [row["system_name"] for row in result["path"]]
        self.assertEqual(names, [fixtures.OCCUPIED, fixtures.FAR])

    def test_check_reachable_reports_disconnected_when_no_path_exists(self) -> None:
        result = pf.check_reachable(
            db_paths=[self.db_path],
            target_system=fixtures.ISOLATED,
            max_jump_ly=15.0,
            max_hops=None,
            candidate_count=10,
            out_dir=None,
        )
        self.assertFalse(result["reachable"])
        self.assertIsNone(result["best_candidate"])
        self.assertIsNone(result["closest_miss"])  # not even connected, so no "closest miss" either
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIsNone(result["candidates"][0]["hop_count"])

    def test_check_reachable_hop_cap_produces_closest_miss(self) -> None:
        # OCCUPIED is 1 hop from FAR; capping at 0 hops means it doesn't
        # qualify, but it's still connected -- so this must be a
        # "closest miss", not a bare "not reachable".
        result = pf.check_reachable(
            db_paths=[self.db_path],
            target_system=fixtures.FAR,
            max_jump_ly=15.0,
            max_hops=0,
            candidate_count=10,
            out_dir=None,
        )
        self.assertFalse(result["reachable"])
        self.assertIsNotNone(result["closest_miss"])
        self.assertEqual(result["closest_miss"]["system_name"], fixtures.OCCUPIED)
        self.assertEqual(result["closest_miss"]["excess_hops"], 1)


if __name__ == "__main__":
    unittest.main()
