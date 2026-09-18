"""dedupe.py clustering: complete linkage, no chaining."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dedupe import cluster, condition_flags  # noqa: E402

LON, LAT = -71.10, 42.52
M = 1 / 111_320  # degrees of latitude per meter


def north(m):
    return (LON, LAT + m * M)


class ClusterTest(unittest.TestCase):
    def test_chain_does_not_merge_through_an_intermediate(self):
        pts = {"a": north(0), "b": north(6), "c": north(12)}
        c = cluster(pts, 8.0)
        self.assertEqual(c["a"], c["b"], "a and b are 6 m apart")
        self.assertNotEqual(c["a"], c["c"], "a and c are 12 m apart, so c stays separate even though b links both")

    def test_all_within_radius_merge_and_far_points_stay_alone(self):
        pts = {"a": north(0), "b": north(3), "c": north(6), "d": north(30)}
        c = cluster(pts, 8.0)
        self.assertEqual(len({c["a"], c["b"], c["c"]}), 1)
        self.assertEqual(len(set(c.values())), 2)

    def test_closest_pair_wins_when_a_point_could_join_either_side(self):
        # b is 5 m from a and 7 m from c; a and c are 12 m apart: b goes with a, c stays alone
        pts = {"a": north(0), "b": north(5), "c": north(12)}
        c = cluster(pts, 8.0)
        self.assertEqual(c["a"], c["b"]); self.assertNotEqual(c["b"], c["c"])

    def test_deterministic_and_empty(self):
        pts = {"x": north(0), "y": north(2)}
        self.assertEqual(cluster(pts, 8.0), cluster(dict(reversed(list(pts.items()))), 8.0))
        self.assertEqual(cluster({}, 8.0), {})


if __name__ == "__main__":
    unittest.main()


class ConditionFlagTest(unittest.TestCase):
    """Mirrors tests/predicates.test.js. The two implementations must agree."""

    CLEAN = {"lean_severity": "none", "crossarm_condition": "intact", "vegetation_contact": "none"}

    def test_double_is_a_condition_flag_and_comes_from_outside_this_record(self):
        self.assertEqual(condition_flags(self.CLEAN), [])
        self.assertEqual(condition_flags(self.CLEAN, is_double=True), ["double"])

    def test_double_combines_with_the_frame_derived_flags_in_a_stable_order(self):
        fields = {**self.CLEAN, "lean_severity": "severe", "vegetation_contact": "touching"}
        self.assertEqual(condition_flags(fields, is_double=True), ["double", "lean", "vegetation"])
        self.assertEqual(condition_flags(fields), ["lean", "vegetation"])
