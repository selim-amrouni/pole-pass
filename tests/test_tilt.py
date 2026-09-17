"""Python-side contracts: the tilt estimator and the grading CSV round trip.

    uv run python3 -m unittest discover -s tests
"""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grade import load_sample, valid_value, write_sample  # noqa: E402
from classify import SCHEMA_VERSION, needs_redo, supersede, write_drop  # noqa: E402
import json  # noqa: E402
from tilt import apparent_tilt  # noqa: E402


def ring(top_x, base_x, half_w=0.01, top_y=0.1, base_y=0.9):
    """A thin quadrilateral from (top_x, top_y) to (base_x, base_y), normalized coords."""
    return [[(top_x - half_w, top_y), (top_x + half_w, top_y), (base_x + half_w, base_y), (base_x - half_w, base_y)]]


class TiltTest(unittest.TestCase):
    def test_vertical_pole_reads_zero(self):
        self.assertEqual(apparent_tilt(ring(0.5, 0.5), 1000, 1000), 0.0)

    def test_sign_follows_top_relative_to_base(self):
        # top right of base by 0.2 of a square image over 0.8 height: atan(0.25) = 14.0 deg
        self.assertAlmostEqual(apparent_tilt(ring(0.6, 0.4), 1000, 1000), 14.0, places=1)
        self.assertAlmostEqual(apparent_tilt(ring(0.4, 0.6), 1000, 1000), -14.0, places=1)

    def test_aspect_ratio_is_applied(self):
        # same normalized outline, image twice as wide: dx doubles in pixels
        self.assertAlmostEqual(apparent_tilt(ring(0.6, 0.4), 2000, 1000), 26.6, places=1)

    def test_too_small_or_degenerate_returns_none(self):
        self.assertIsNone(apparent_tilt(ring(0.5, 0.5, top_y=0.50, base_y=0.51), 1000, 1000))  # 10 px tall
        self.assertIsNone(apparent_tilt([[(0.5, 0.1), (0.5, 0.9)]], 1000, 1000))  # two points
        self.assertIsNone(apparent_tilt([], 1000, 1000))
        self.assertIsNone(apparent_tilt(None, 1000, 1000))
        self.assertIsNone(apparent_tilt(ring(0.5, 0.5), None, 1000))


class GradeCsvTest(unittest.TestCase):
    HEADER = ["pole_id", "crop", "model_lean", "truth_is_utility_pole", "truth_lean", "grader_notes"]
    ROWS = [["# how to grade", "", "", "y/n", "none/slight/moderate/severe", ""],
            ["gree-00001", "data/crops/1.jpg", "severe", "", "", ""],
            ["gree-00002", "data/crops/2.jpg", "none", "y", "slight", "has a \"quote\", and a comma"]]

    def test_round_trip_keeps_header_order_and_help_row(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sample.csv"
            with p.open("w", newline="") as fh:
                w = csv.writer(fh); w.writerow(self.HEADER); w.writerows(self.ROWS)
            header, rows = load_sample(p)
            self.assertEqual(header, self.HEADER)
            self.assertEqual(rows[0]["pole_id"], "# how to grade")
            rows[1]["truth_lean"] = "moderate"
            write_sample(p, header, rows)
            header2, rows2 = load_sample(p)
            self.assertEqual(header2, self.HEADER)
            self.assertEqual([r["pole_id"] for r in rows2], ["# how to grade", "gree-00001", "gree-00002"])
            self.assertEqual(rows2[1]["truth_lean"], "moderate")
            self.assertEqual(rows2[2]["grader_notes"], 'has a "quote", and a comma')
            self.assertEqual(rows2[2]["model_lean"], "none", "model columns untouched")

    def test_empty_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sample.csv"; p.write_text("")
            with self.assertRaises(ValueError):
                load_sample(p)

    def test_only_values_the_scorer_understands_are_accepted(self):
        self.assertTrue(valid_value("truth_lean", "slight")); self.assertTrue(valid_value("truth_lean", ""))
        self.assertFalse(valid_value("truth_lean", "maybe")); self.assertFalse(valid_value("truth_lean", ["slight"]))
        self.assertTrue(valid_value("truth_is_utility_pole", "n")); self.assertFalse(valid_value("truth_is_utility_pole", "yes"))
        self.assertTrue(valid_value("truth_attachment_count", "3")); self.assertFalse(valid_value("truth_attachment_count", "3.5"))
        self.assertTrue(valid_value("grader_notes", "free text")); self.assertFalse(valid_value("grader_notes", "x" * 2001))


class RedoLeanTest(unittest.TestCase):
    """--redo-lean resends only old-schema results whose lean call is listed; dropped rows and current results stay cached."""

    def test_selection(self):
        old_sev = {"result": {"lean_severity": "severe"}}
        old_mod = {"result": {"lean_severity": "moderate"}, "schema": 1}
        old_none = {"result": {"lean_severity": "none"}}
        new_sev = {"result": {"lean_severity": "severe"}, "schema": SCHEMA_VERSION}
        dropped = {"dropped": "pole_too_small"}
        leans = {"moderate", "severe"}
        self.assertTrue(needs_redo(old_sev, leans)); self.assertTrue(needs_redo(old_mod, leans))
        self.assertFalse(needs_redo(old_none, leans)); self.assertFalse(needs_redo(new_sev, leans))
        self.assertFalse(needs_redo(dropped, leans)); self.assertFalse(needs_redo(old_sev, set()))

    def test_supersede_keeps_every_generation_and_a_failed_rerun_restores(self):
        with tempfile.TemporaryDirectory() as d:
            res = Path(d); p = res / "42.json"
            p.write_text(json.dumps({"result": {"lean_severity": "severe"}}))
            supersede(p, json.loads(p.read_text()))
            self.assertFalse(p.exists()); self.assertTrue((res / "42.v1.json").exists())
            self.assertEqual(write_drop(res, "42", "api:500"), "restored")
            self.assertEqual(json.loads(p.read_text())["result"]["lean_severity"], "severe"); self.assertFalse((res / "42.v1.json").exists())
            p.write_text(json.dumps({"result": {}, "schema": 2})); supersede(p, json.loads(p.read_text()))
            self.assertTrue((res / "42.v2.json").exists())
            self.assertEqual(write_drop(res, "7", "malformed"), "dropped"); self.assertTrue(json.loads((res / "7.json").read_text())["dropped"])


if __name__ == "__main__":
    unittest.main()
