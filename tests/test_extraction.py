"""Tests for scripts/extract_sector_systems_to_sqlite.py against the shared
fixture sector (tests/fixtures.py)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import extract_sector_systems_to_sqlite as extractor
from tests import fixtures


class ExtractionPureFunctionTests(unittest.TestCase):
    def test_sanitize_prefix(self) -> None:
        self.assertEqual(extractor.sanitize_prefix("Parrots Head Sector"), "Parrots_Head_Sector")
        self.assertEqual(extractor.sanitize_prefix("  Eotchorts  "), "Eotchorts")
        self.assertEqual(extractor.sanitize_prefix(""), "sector")

    def test_matches_prefix_token_boundary(self) -> None:
        # "Oochost" must not match an unrelated "Oochostia XY-Z" -- only a
        # whole-token prefix (exact name, or followed by a space) counts.
        self.assertFalse(extractor.matches_prefix("Oochostia XY-Z", "Oochost", case_insensitive=True))
        self.assertTrue(extractor.matches_prefix("Eotchorts EG-X d1-265", "Eotchorts", case_insensitive=True))
        self.assertTrue(extractor.matches_prefix("eotchorts EG-X d1-265", "Eotchorts", case_insensitive=True))
        self.assertFalse(extractor.matches_prefix("eotchorts EG-X d1-265", "Eotchorts", case_insensitive=False))
        self.assertTrue(extractor.matches_prefix("Eotchorts", "Eotchorts", case_insensitive=True))


class ExtractionFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = fixtures.build_fixture_db(Path(self._tmp.name))

    def _query(self, sql: str, params: tuple = ()) -> list:
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_all_systems_extracted(self) -> None:
        rows = self._query("SELECT name FROM systems ORDER BY name")
        names = [r[0] for r in rows]
        self.assertEqual(
            names,
            [fixtures.ORIGIN, fixtures.RICH, fixtures.FAR, fixtures.OCCUPIED, fixtures.ISOLATED],
        )

    def test_body_counts_per_system(self) -> None:
        rows = self._query("SELECT system_name, COUNT(*) FROM bodies GROUP BY system_name")
        counts = dict(rows)
        # RICH: 1 star + 8 planets + 1 barycentre = 10 body rows (bodies
        # table includes barycentres; total_body_count scoring excludes
        # them separately -- see test_reachability.py).
        self.assertEqual(counts[fixtures.RICH], 10)
        self.assertEqual(counts[fixtures.ORIGIN], 2)
        self.assertEqual(counts[fixtures.FAR], 2)
        self.assertEqual(counts[fixtures.OCCUPIED], 2)
        self.assertEqual(counts[fixtures.ISOLATED], 2)

    def test_government_round_trips_through_raw_json(self) -> None:
        rows = self._query("SELECT name, raw_json FROM systems")
        gov_by_name = {name: json.loads(raw).get("government") for name, raw in rows}
        self.assertEqual(gov_by_name[fixtures.OCCUPIED], "Democracy")
        self.assertEqual(gov_by_name[fixtures.ISOLATED], "None")
        self.assertIsNone(gov_by_name[fixtures.ORIGIN])
        self.assertIsNone(gov_by_name[fixtures.RICH])

    def test_ring_flattened_columns_use_spansh_keys(self) -> None:
        """The extractor reads Spansh's type/mass/innerRadius/outerRadius
        into the flattened ring columns (they were NULL before v1.1.0, so
        gui/db_lookup.py still reads raw_json for older databases)."""
        rows = self._query(
            "SELECT ring_name, ring_class, mass_mt, inner_rad, outer_rad, raw_json FROM rings"
        )
        self.assertEqual(len(rows), 1)
        ring_name, ring_class, mass_mt, inner_rad, outer_rad, raw_json = rows[0]
        self.assertEqual(ring_class, fixtures.ICY_RING["type"])
        self.assertEqual(mass_mt, fixtures.ICY_RING["mass"])
        self.assertEqual(inner_rad, fixtures.ICY_RING["innerRadius"])
        self.assertEqual(outer_rad, fixtures.ICY_RING["outerRadius"])
        data = json.loads(raw_json)
        self.assertEqual(data["type"], "Icy")
        self.assertEqual(data["mass"], 228600000000.0)


if __name__ == "__main__":
    unittest.main()
