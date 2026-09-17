"""doubles.py: pair id stability, the grid screen, shared-frame selection, and street naming.
No network.

    uv run python3 -m unittest discover -s tests
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doubles import (  # noqa: E402
    make_pair_id, nearest_named_way, pair_screen, select_frames, shared_detections,
)

LAT = 42.50  # roughly Marblehead's latitude, so the meter offsets below are realistic


def lon_offset_for_m(m, lat=LAT):
    return m / (111_320.0 * math.cos(math.radians(lat)))


def lat_offset_for_m(m):
    return m / 111_320.0


def det(image_id):
    """A minimal detection dict -- only the 'image.id' shape shared_detections/select_frames
    look at before deciding whether geometry needs decoding at all."""
    return {"image": {"id": image_id}}


class PairIdTest(unittest.TestCase):
    def test_stable_and_order_independent(self):
        a = make_pair_id("111", "222")
        b = make_pair_id("222", "111")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("MH-"))

    def test_different_pairs_get_different_ids(self):
        self.assertNotEqual(make_pair_id("1", "2"), make_pair_id("1", "3"))

    def test_stable_across_calls(self):
        # not just order-independent within one call -- the same pair must hash the same every run,
        # since pair_id is the only handle later steps (results cache, crops) key off of
        self.assertEqual(make_pair_id("111", "222"), make_pair_id("111", "222"))


class PairScreenTest(unittest.TestCase):
    def test_finds_close_pair_and_rejects_far_pair(self):
        points = {
            "a": (0.0, LAT),
            "b": (lon_offset_for_m(3.0), LAT),          # ~3 m from a
            "c": (0.02, LAT),
            "d": (0.02 + lon_offset_for_m(20.0), LAT),   # ~20 m from c
        }
        pairs = pair_screen(points, radius_m=6.0)
        found = {frozenset((x, y)) for x, y, _ in pairs}
        self.assertIn(frozenset(("a", "b")), found, "3 m pair must pass a 6 m radius screen")
        self.assertNotIn(frozenset(("c", "d")), found, "20 m pair must not pass a 6 m radius screen")

    def test_no_pairs_when_nothing_is_close(self):
        points = {"a": (0.0, LAT), "b": (0.02, LAT)}
        self.assertEqual(pair_screen(points, radius_m=6.0), [])

    def test_each_pair_reported_once(self):
        points = {"a": (0.0, LAT), "b": (lon_offset_for_m(3.0), LAT)}
        pairs = pair_screen(points, radius_m=6.0)
        self.assertEqual(len(pairs), 1)


class SharedDetectionsTest(unittest.TestCase):
    def test_intersection_only(self):
        dets_a = [det("1"), det("2")]
        dets_b = [det("2"), det("3")]
        shared = shared_detections(dets_a, dets_b)
        self.assertEqual(set(shared), {"2"})

    def test_disjoint_sets_share_nothing(self):
        self.assertEqual(shared_detections([det("1")], [det("2")]), {})


class SelectFramesTest(unittest.TestCase):
    def test_no_shared_frame_survives_as_a_status_not_a_drop(self):
        status, kept = select_frames([det("1")], [det("2")], frames_n=2)
        self.assertEqual(status, "no_shared_frame")
        self.assertEqual(kept, [])


class NearestNamedWayTest(unittest.TestCase):
    def setUp(self):
        # two named streets a known distance apart, plus an unnamed way that must never be picked
        self.ways = [
            {"name": "Elm Street", "coords": [(0.0, LAT), (0.01, LAT)]},
            {"name": "Oak Street", "coords": [(0.0, LAT + lat_offset_for_m(50.0)), (0.01, LAT + lat_offset_for_m(50.0))]},
            {"name": None, "coords": [(0.0, LAT + lat_offset_for_m(0.1)), (0.01, LAT + lat_offset_for_m(0.1))]},
        ]

    def test_cross_street_is_never_the_same_name_as_street(self):
        point = (0.005, LAT)
        street, _ = nearest_named_way(*point, self.ways)
        cross, _ = nearest_named_way(*point, self.ways, exclude_name=street)
        self.assertEqual(street, "Elm Street")
        self.assertIsNotNone(cross)
        self.assertNotEqual(street, cross)

    def test_cross_street_respects_max_distance(self):
        point = (0.005, LAT)
        cross, cross_d = nearest_named_way(*point, self.ways, exclude_name="Elm Street", max_m=10.0)
        self.assertIsNone(cross)  # Oak Street is ~50 m away, past the 10 m cap
        self.assertIsNone(cross_d)

    def test_unnamed_ways_are_never_returned(self):
        name, _ = nearest_named_way(0.005, LAT + lat_offset_for_m(0.1), self.ways)
        self.assertIn(name, ("Elm Street", "Oak Street"))


if __name__ == "__main__":
    unittest.main()
