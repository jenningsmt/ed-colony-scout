"""Regression tests for the defects found by the October 2026 engineering audit.

Each test asserts the intended (fixed) behavior. Tests for defects that are not
fixed yet are marked @known_defect, so they count as expected failures and the
suite stays green; the Phase 1 fix for each one removes its marker. Set
SHOW_KNOWN_DEFECTS=1 to run them as ordinary tests and see the current failures.

Finding IDs (E01, E02, ...) refer to the Zurara Bridge engineering review.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gui import worker as worker_module
from scripts import colonization_reachability_export as reach
from scripts import extract_multi_sector_to_sqlite as multi
from scripts import extract_sector_systems_to_sqlite as extractor
from scripts import path_finder_export as path_finder

known_defect = (lambda f: f) if os.environ.get("SHOW_KNOWN_DEFECTS") else unittest.expectedFailure


def _system(name="Audit AA-A a0", address=1, coords=None):
    row = {"name": name, "coords": coords or {"x": 0, "y": 0, "z": 0}, "bodies": []}
    if address is not None:
        row["id64"] = address
    return row


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count_systems(db: Path) -> int:
    with contextlib.closing(sqlite3.connect(db)) as conn:
        return conn.execute("select count(*) from systems").fetchone()[0]


class WorkerStatusTests(unittest.TestCase):
    def test_exception_reports_failure_status(self):
        """E01: a job that raises must not finish with __job_done__:0."""
        w = worker_module.Worker()

        def broken(**_kwargs):
            raise RuntimeError("injected failure")

        w._run(broken, (), {})
        messages = []
        while not w.log_queue.empty():
            messages.append(w.log_queue.get_nowait())
        self.assertTrue(any("RuntimeError" in m for m in messages))
        self.assertNotEqual(messages[-1], f"{worker_module.DONE_SENTINEL}:0")

    def test_normal_completion_reports_its_return_code(self):
        w = worker_module.Worker()
        w._run(lambda **_kwargs: 0, (), {})
        messages = []
        while not w.log_queue.empty():
            messages.append(w.log_queue.get_nowait())
        self.assertEqual(messages[-1], f"{worker_module.DONE_SENTINEL}:0")


class ExtractionDefectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.dump = self.tmp / "dump.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_final_flush_failure_returns_nonzero(self):
        """E01: a failed final flush in the multi-sector extractor must not return 0."""
        _write_jsonl(self.dump, [json.dumps(_system())])
        sinks = []
        build_sinks = multi.build_sinks

        def capture(*args, **kwargs):
            built = build_sinks(*args, **kwargs)
            sinks.extend(built)
            return built

        try:
            with mock.patch.object(multi, "build_sinks", side_effect=capture), \
                    mock.patch.object(multi.SectorSink, "flush", side_effect=sqlite3.OperationalError("injected disk failure")), \
                    contextlib.redirect_stdout(io.StringIO()):
                rc = multi.run(self.dump, ["Audit"], self.tmp / "out")
            self.assertNotEqual(rc, 0)
        finally:
            # The failed flush currently skips closing the connection (also part of E01);
            # close it here so Windows can delete the temporary folder.
            for sink in sinks:
                sink.conn.close()

    def test_corrupt_jsonl_line_is_reported(self):
        """E02: a malformed line must be counted and reported, not skipped silently."""
        _write_jsonl(self.dump, [json.dumps(_system()), "{invalid json}", json.dumps(_system("Audit AA-A a1", 2))])
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            rc = extractor.run(self.dump, "Audit", output_db=self.tmp / "corrupt.sqlite")
        self.assertEqual(_count_systems(self.tmp / "corrupt.sqlite"), 2)
        self.assertNotEqual(rc, 0)
        self.assertIn("rejected", log.getvalue().lower())

    def test_limit_one_extracts_one_system(self):
        """P2: limit=1 must extract one record, not zero."""
        _write_jsonl(self.dump, [json.dumps(_system())])
        with contextlib.redirect_stdout(io.StringIO()):
            rc = extractor.run(self.dump, "Audit", output_db=self.tmp / "limit.sqlite", limit=1)
        self.assertEqual(rc, 0)
        self.assertEqual(_count_systems(self.tmp / "limit.sqlite"), 1)


class CatalogInputTests(unittest.TestCase):
    def test_missing_coordinate_is_quarantined_by_loader(self):
        """E02: a stored (0, None, 0) position must not reach the graph builder."""
        row, _, _ = extractor.process_system(_system(coords={"x": 0, "y": None, "z": 0}), "Audit", True)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "missing.sqlite"
            with contextlib.closing(sqlite3.connect(db)) as conn:
                extractor.init_db(conn)
                extractor.upsert_systems(conn, [row])
                conn.commit()
            with contextlib.redirect_stdout(io.StringIO()):
                merged = reach.load_merged_systems([db])
        for coord in merged.coords:
            self.assertTrue(all(isinstance(v, float) for v in coord))
        reach.build_adjacency(merged.coords, 15.0)

    def test_malformed_metadata_is_not_eligible(self):
        """E02 / D2: malformed or non-object raw_json is unknown, never eligible, and never raises."""
        for raw in ("{broken", "[]", "null"):
            with self.subTest(raw=raw):
                self.assertIsNot(reach.is_colonization_eligible(raw), True)

    def test_invalid_jump_radius_is_rejected(self):
        """E02: zero, negative, infinite and NaN radii must raise ValueError."""
        for radius in (0.0, -15.0, float("inf"), float("nan")):
            with self.subTest(radius=radius):
                with self.assertRaises(ValueError):
                    reach.build_adjacency([(0.0, 0.0, 0.0), (30.0, 0.0, 0.0)], radius)

    def test_empty_singleton_and_disconnected_graphs(self):
        """Already correct: degenerate graphs are ordinary results."""
        self.assertEqual(dict(reach.build_adjacency([], 15.0)), {})
        hops, _ = path_finder.bfs_with_parents(reach.build_adjacency([(0.0, 0.0, 0.0)], 15.0), 0)
        self.assertEqual(hops, {0: 0})
        hops, _ = path_finder.bfs_with_parents(reach.build_adjacency([(0.0, 0.0, 0.0), (16.0, 0.0, 0.0)], 15.0), 0)
        self.assertEqual(hops, {0: 0})


class ScoringParameterLimitTests(unittest.TestCase):
    def test_scoring_accepts_more_keys_than_sqlite_variable_limit(self):
        """E05: scoring must not build one IN (...) parameter per key."""
        conn = sqlite3.connect(":memory:")
        try:
            extractor.init_db(conn)
            limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
            result = reach.score_systems(conn, [f"addr:{i}" for i in range(limit + 1)])
        finally:
            conn.close()
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
