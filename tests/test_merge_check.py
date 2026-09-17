"""merge_check.py: sampling, CSV round trip, and the over-merge score. No server, no network."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merge_check import COLS, load_csv, sample, score, write_csv  # noqa: E402


def rec(i, n=2):
    return {"pole_id": f"t-{i:05d}", "n_features": n, "feature_ids": ";".join(str(100 + k) for k in range(n)), "max_pairwise_m": 5.0 + i}


class MergeCheckTest(unittest.TestCase):
    def test_sample_is_reproducible_and_never_repeats(self):
        recs = [rec(i) for i in range(50)]
        a = sample(recs, [], 10); b = sample(recs, [], 10)
        self.assertEqual([r["pole_id"] for r in a], [r["pole_id"] for r in b])
        self.assertEqual(len(a), 10); self.assertTrue(all(r["verdict"] == "" for r in a))
        more = sample(recs, a, 5)
        self.assertEqual(len(more), 15); self.assertEqual(len({r["pole_id"] for r in more}), 15)
        self.assertEqual(more[:10], a, "existing rows and their order are kept")
        self.assertEqual(len(sample(recs[:3], [], 10)), 3, "cannot sample more than exist")

    def test_csv_round_trip_keeps_answers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "merges.csv"
            rows = sample([rec(i) for i in range(4)], [], 4)
            rows[1]["verdict"] = "different"; rows[1]["notes"] = 'corner, two poles "clearly"'
            write_csv(p, rows)
            back = load_csv(p)
            self.assertEqual(list(back[0].keys()), COLS)
            self.assertEqual(back[1]["verdict"], "different"); self.assertEqual(back[1]["notes"], 'corner, two poles "clearly"')
            self.assertEqual(back[0]["n_features"], "2")
            self.assertEqual(load_csv(Path(d) / "missing.csv"), [])

    def test_score_counts_only_answered_rows(self):
        rows = [dict(rec(i), verdict=v) for i, v in enumerate(["same", "different", "unsure", "", "same", "different"])]
        s = score(rows)
        self.assertEqual((s["sampled"], s["answered"], s["unsure"], s["same"], s["different"]), (6, 4, 1, 2, 2))
        self.assertEqual(s["over_merge_rate"], 0.5); self.assertEqual(s["different_ids"], ["t-00001", "t-00005"])
        self.assertIsNone(score([])["over_merge_rate"])


if __name__ == "__main__":
    unittest.main()
