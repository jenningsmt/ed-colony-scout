"""Tests for gui/system_detail.py's pure-logic pieces: the signal/genus
label-cleanup regexes and the static row/summary formatters. These don't
touch Tk widgets (the formatters are @staticmethod, callable directly on
the class), so they're safe to run on a display-less CI runner even though
the module itself imports tkinter."""

from __future__ import annotations

import unittest

from gui import system_detail
from gui.db_lookup import BodyDetail, RingDetail, SystemDetail


class CleanupHelperTests(unittest.TestCase):
    def test_clean_signal_type(self) -> None:
        self.assertEqual(system_detail._clean_signal_type("$SAA_SignalType_Biological;"), "Biological")
        self.assertEqual(system_detail._clean_signal_type("$SAA_SignalType_Geological;"), "Geological")
        self.assertEqual(system_detail._clean_signal_type("UnrecognizedKey"), "UnrecognizedKey")

    def test_clean_genus_name(self) -> None:
        self.assertEqual(system_detail._clean_genus_name("$Codex_Ent_Bacterial_Genus_Name;"), "Bacterial")
        self.assertEqual(system_detail._clean_genus_name("$Codex_Ent_Shrubs_Genus_Name;"), "Shrubs")
        self.assertEqual(system_detail._clean_genus_name("UnrecognizedKey"), "UnrecognizedKey")


class RowFormattingTests(unittest.TestCase):
    def test_body_row_landable_and_signal_breakdown(self) -> None:
        body = BodyDetail(
            body_name="Test 1",
            body_type="Planet",
            sub_type="Earth-like world",
            terraform_state="Not terraformable",
            is_landable=True,
            signal_counts={"$SAA_SignalType_Biological;": 7, "$SAA_SignalType_Geological;": 2},
            genuses=["$Codex_Ent_Bacterial_Genus_Name;", "$Codex_Ent_Shrubs_Genus_Name;"],
        )
        row = system_detail.SystemDetailDialog._body_row(body)
        self.assertEqual(row["landable"], "Yes")
        self.assertEqual(row["biosignals"], "7")
        self.assertEqual(row["geosignals"], "2")
        self.assertIn("Bacterial", row["genuses"])
        self.assertIn("Shrubs", row["genuses"])

    def test_body_row_not_landable_and_no_signals(self) -> None:
        body = BodyDetail(
            body_name="Test 2", body_type="Planet", sub_type="Rocky body",
            terraform_state="Not terraformable", is_landable=False,
        )
        row = system_detail.SystemDetailDialog._body_row(body)
        self.assertEqual(row["landable"], "No")
        self.assertEqual(row["biosignals"], "")
        self.assertEqual(row["geosignals"], "")
        self.assertEqual(row["genuses"], "")

    def test_body_row_star_landable_blank_not_no(self) -> None:
        # Stars/barycentres have is_landable=None -- must render blank, not
        # a misleading "No" (which would imply the game reports it as
        # explicitly non-landable, when really the concept doesn't apply).
        star = BodyDetail(
            body_name="Test A", body_type="Star", sub_type="G (White-Yellow) Star",
            terraform_state=None, is_landable=None,
        )
        row = system_detail.SystemDetailDialog._body_row(star)
        self.assertEqual(row["landable"], "")

    def test_body_row_other_signal_type_shown_alongside_geosignals(self) -> None:
        # Only Biological/Geological occur in real data today, but the
        # summary/row logic must not silently drop an unrecognized type.
        body = BodyDetail(
            body_name="Test 3", body_type="Planet", sub_type="Icy body",
            terraform_state="Not terraformable", is_landable=False,
            signal_counts={"$SAA_SignalType_Thargoid;": 4},
        )
        row = system_detail.SystemDetailDialog._body_row(body)
        self.assertIn("Thargoid", row["geosignals"])
        self.assertIn("4", row["geosignals"])

    def test_ring_row_formats_numbers(self) -> None:
        ring = RingDetail(
            body_name="Test 7", ring_name="Test 7 A Ring", ring_class="Icy",
            mass_mt=228600000000.0, inner_rad=61071000.0, outer_rad=105120000.0,
        )
        row = system_detail.SystemDetailDialog._ring_row(ring)
        self.assertEqual(row["ring_class"], "Icy")
        self.assertEqual(row["mass_mt"], "228,600,000,000")

    def test_summary_text_counts(self) -> None:
        detail = SystemDetail(
            system_name="Test System", x=0.0, y=0.0, z=0.0,
            bodies=[
                BodyDetail("A", "Star", "G Star", None, None),
                BodyDetail("1", "Planet", "Earth-like world", "Not terraformable", True,
                           signal_counts={"$SAA_SignalType_Biological;": 7}),
                BodyDetail("2", "Planet", "Rocky body", "Not terraformable", False,
                           signal_counts={"$SAA_SignalType_Geological;": 2}),
            ],
            rings=[RingDetail("2", "2 A Ring", "Icy", 1.0, 1.0, 2.0)],
        )
        summary = system_detail.SystemDetailDialog._summary_text(detail)
        self.assertIn("3 bodies", summary)
        self.assertIn("1 landable", summary)
        self.assertIn("7 biosignals", summary)
        self.assertIn("1 ring(s)", summary)


if __name__ == "__main__":
    unittest.main()
