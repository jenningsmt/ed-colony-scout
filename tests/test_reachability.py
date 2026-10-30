"""Tests for scripts/colonization_reachability_export.py: pure scoring-math
functions directly, plus an end-to-end run() against the shared fixture
sector (tests/fixtures.py)."""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
from pathlib import Path

from scripts import colonization_reachability_export as reach
from tests import fixtures


class ScoringMathTests(unittest.TestCase):
    def test_score_tiered(self) -> None:
        # 2 at first_pts, remaining 3 at rest_pts.
        self.assertEqual(reach.score_tiered(5, first_n=2, first_pts=100.0, rest_pts=50.0), 350.0)
        self.assertEqual(reach.score_tiered(0, first_n=2, first_pts=100.0, rest_pts=50.0), 0.0)
        self.assertEqual(reach.score_tiered(1, first_n=2, first_pts=100.0, rest_pts=50.0), 100.0)

    def test_ring_density(self) -> None:
        mass, inner, outer = 228600000000.0, 61071000.0, 105120000.0
        expected = mass / (math.pi * (outer**2 - inner**2))
        self.assertAlmostEqual(reach.ring_density(mass, inner, outer), expected)

    def test_ring_density_degenerate_cases(self) -> None:
        self.assertEqual(reach.ring_density(0.0, 1.0, 2.0), 0.0)
        self.assertEqual(reach.ring_density(100.0, 2.0, 1.0), 0.0)  # outer <= inner
        self.assertEqual(reach.ring_density(100.0, 0.0, 1.0), 0.0)  # inner <= 0

    def test_score_ring(self) -> None:
        rubric = reach.STOCK_RUBRIC
        self.assertEqual(reach.score_ring(2.0, "Metallic", rubric), 30.0 * (1.0 + (2.0 - 1.0) * 0.5))
        self.assertEqual(reach.score_ring(1.0, "Icy", rubric), 25.0)  # ratio 1.0 -> base points exactly

    def test_is_colonization_eligible(self) -> None:
        self.assertTrue(reach.is_colonization_eligible("{}"))  # no government field: the dump's best signal
        self.assertTrue(reach.is_colonization_eligible('{"government": "None"}'))
        self.assertTrue(reach.is_colonization_eligible('{"government": null}'))
        self.assertFalse(reach.is_colonization_eligible('{"government": "Democracy"}'))
        # Unknown is never eligible (v1.1.0; these defaulted to eligible before).
        self.assertFalse(reach.is_colonization_eligible(None))
        self.assertFalse(reach.is_colonization_eligible("not json"))

    def test_colonization_status_has_three_states(self) -> None:
        self.assertEqual(reach.colonization_status('{"government": "None"}'), reach.ELIGIBLE)
        self.assertEqual(reach.colonization_status('{"government": "Feudal"}'), reach.INELIGIBLE)
        for raw in (None, "", "not json", "[]", "null", "42"):
            with self.subTest(raw=raw):
                self.assertEqual(reach.colonization_status(raw), reach.UNKNOWN)

    def test_build_key_scoring_factors_orders_by_contribution(self) -> None:
        factors = reach.build_key_scoring_factors({
            "elw_score": 100.0, "ww_score": 200.0, "hmct_score": 0.0,
            "metallic_ring_score": 0.0, "icy_ring_score": 50.0,
        })
        self.assertEqual(factors, "WW+ELW+Icy")

    def test_build_key_scoring_factors_empty_when_all_zero(self) -> None:
        self.assertEqual(reach.build_key_scoring_factors({}), "")


class ReachabilityFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db_path = fixtures.build_fixture_db(Path(self._tmp.name))
        out_dir = Path(self._tmp.name) / "out"
        self.candidates_path, self.dense_path = reach.run(
            db_paths=[db_path],
            origin_system=fixtures.ORIGIN,
            max_jump_ly=15.0,
            max_hops=5,
            out_dir=out_dir,
            sector_label=fixtures.SECTOR_PREFIX,
            dense_top_n=20,
            rubric=reach.STOCK_RUBRIC,
        )
        with self.candidates_path.open(newline="", encoding="utf-8") as f:
            self.candidates = {row["system_name"]: row for row in csv.DictReader(f)}

    def test_output_files_written(self) -> None:
        self.assertTrue(self.candidates_path.exists())
        self.assertTrue(self.dense_path.exists())

    def test_occupied_system_excluded_from_candidates(self) -> None:
        self.assertNotIn(fixtures.OCCUPIED, self.candidates)

    def test_isolated_system_not_reachable(self) -> None:
        self.assertNotIn(fixtures.ISOLATED, self.candidates)

    def test_far_system_reached_via_two_hop_chain(self) -> None:
        # FAR is 20ly from ORIGIN (no direct 15ly edge) but 10ly from RICH,
        # so it must show hop_count == 2, proving the chain-BFS (not a
        # straight-line radius) is what's being tested.
        self.assertIn(fixtures.FAR, self.candidates)
        self.assertEqual(int(self.candidates[fixtures.FAR]["hop_count"]), 2)

    def test_rich_system_scoring_breakdown(self) -> None:
        row = self.candidates[fixtures.RICH]
        self.assertEqual(int(row["hop_count"]), 1)
        self.assertEqual(int(row["elw_count"]), 2)
        self.assertEqual(int(row["ww_count"]), 1)
        # Only the terraformable HMC world counts -- the non-terraformable
        # one must NOT be counted.
        self.assertEqual(int(row["hmct_terraformable_count"]), 1)
        self.assertEqual(float(row["elw_score"]), 200.0)
        self.assertEqual(float(row["ww_score"]), 40.0)
        self.assertEqual(float(row["hmct_score"]), 30.0)
        # Icy ring is above the galactic median density floor -> scores;
        # no metallic ring exists in the fixture at all.
        self.assertEqual(row["metallic_ring_score"], "")
        self.assertGreater(float(row["icy_ring_score"]), 0.0)
        expected_total = 200.0 + 40.0 + 30.0 + float(row["icy_ring_score"])
        self.assertAlmostEqual(float(row["colonization_score"]), round(expected_total, 1), places=1)

    def test_ranks_are_sequential_and_score_sorted(self) -> None:
        rows = sorted(self.candidates.values(), key=lambda r: int(r["rank"]))
        ranks = [int(r["rank"]) for r in rows]
        self.assertEqual(ranks, list(range(1, len(rows) + 1)))
        scores = [float(r["colonization_score"]) for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
