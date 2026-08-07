"""Tests for gui/db_lookup.py against the shared fixture sector
(tests/fixtures.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gui import db_lookup
from tests import fixtures


class DbLookupFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = str(fixtures.build_fixture_db(Path(self._tmp.name)))

    def test_system_exists(self) -> None:
        self.assertTrue(db_lookup.system_exists([self.db_path], fixtures.ORIGIN))
        self.assertFalse(db_lookup.system_exists([self.db_path], "Nonexistent System XYZ"))

    def test_search_system_names(self) -> None:
        results = db_lookup.search_system_names([self.db_path], "a2")
        self.assertEqual(results, [fixtures.RICH])
        self.assertEqual(db_lookup.search_system_names([self.db_path], ""), [])

    def test_get_system_detail_not_found(self) -> None:
        self.assertIsNone(db_lookup.get_system_detail([self.db_path], "Nonexistent System XYZ"))

    def test_get_system_detail_body_and_ring_breakdown(self) -> None:
        detail = db_lookup.get_system_detail([self.db_path], fixtures.RICH)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.system_name, fixtures.RICH)
        # 1 star + 8 planets + 1 barycentre, per the fixture definition.
        self.assertEqual(len(detail.bodies), 10)

        by_name = {b.body_name: b for b in detail.bodies}

        bio_body = by_name[f"{fixtures.RICH} 6"]
        self.assertEqual(bio_body.signal_counts, {"$SAA_SignalType_Biological;": 7})
        self.assertEqual(
            sorted(bio_body.genuses),
            sorted(["$Codex_Ent_Shrubs_Genus_Name;", "$Codex_Ent_Bacterial_Genus_Name;"]),
        )

        geo_body = by_name[f"{fixtures.RICH} 8"]
        self.assertEqual(geo_body.signal_counts, {"$SAA_SignalType_Geological;": 3})
        self.assertEqual(geo_body.genuses, [])

        terraformable_hmc = by_name[f"{fixtures.RICH} 4"]
        self.assertTrue(terraformable_hmc.is_landable)
        self.assertEqual(terraformable_hmc.terraform_state, "Terraformable")

        non_terraformable_hmc = by_name[f"{fixtures.RICH} 5"]
        self.assertEqual(non_terraformable_hmc.terraform_state, "Not terraformable")

        star = by_name[f"{fixtures.RICH} A"]
        self.assertIsNone(star.is_landable)

        # Proves the raw_json-based ring-parsing workaround (gui/db_lookup.
        # _parse_ring_detail) works against a freshly-extracted DB where the
        # flattened rings columns are NULL -- see
        # test_extraction.test_ring_flattened_columns_are_null_known_extraction_quirk.
        self.assertEqual(len(detail.rings), 1)
        ring = detail.rings[0]
        self.assertEqual(ring.ring_class, "Icy")
        self.assertEqual(ring.mass_mt, 228600000000.0)
        self.assertEqual(ring.inner_rad, 61071000.0)
        self.assertEqual(ring.outer_rad, 105120000.0)

    def test_get_system_detail_no_rings(self) -> None:
        detail = db_lookup.get_system_detail([self.db_path], fixtures.ORIGIN)
        self.assertIsNotNone(detail)
        self.assertEqual(detail.rings, [])


if __name__ == "__main__":
    unittest.main()
