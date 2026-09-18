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
    adjudicate, frame_separation, make_pair_id, nearest_named_way, pair_prefix,
    pair_screen, select_frames, shared_detections,
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
        a = make_pair_id("111", "222", "MH")
        b = make_pair_id("222", "111", "MH")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("MH-"))

    def test_different_pairs_get_different_ids(self):
        self.assertNotEqual(make_pair_id("1", "2", "MH"), make_pair_id("1", "3", "MH"))

    def test_stable_across_calls(self):
        # not just order-independent within one call -- the same pair must hash the same every run,
        # since pair_id is the only handle later steps (results cache, crops) key off of
        self.assertEqual(make_pair_id("111", "222", "MH"), make_pair_id("111", "222", "MH"))

    def test_prefix_separates_towns_sharing_the_global_crop_cache(self):
        self.assertNotEqual(make_pair_id("111", "222", "MH"), make_pair_id("111", "222", "NEW"))

    def test_marblehead_prefix_is_pinned_so_its_cached_run_still_resolves(self):
        # data/doubles/marblehead-massachusetts/results/ and data/doubles/crops/ are already keyed
        # MH-; deriving the prefix from the slug instead would orphan every one of them.
        self.assertEqual(pair_prefix("marblehead-massachusetts"), "MH")
        self.assertEqual(pair_prefix("newton-massachusetts"), "NEW")


class FrameSeparationTest(unittest.TestCase):
    """Separation measured off the two detection boxes, which is what replaced two bad numbers:
    the haversine between triangulated map features, and the model's own visual guess at metres."""

    def test_touching_poles_do_not_report_metres_apart(self):
        # The Marblehead regression: MH-dd2a587e's boxes overlap horizontally (the poles visibly
        # cross in the photo) yet it was reported as 4 m by the model and 5.90 m by the map.
        a = (1831 / 4096, 572 / 2048, 1855 / 4096, 830 / 2048)
        b = (1845 / 4096, 664 / 2048, 1862 / 4096, 796 / 2048)
        s = frame_separation(a, b)
        self.assertTrue(s["boxes_overlap"])
        self.assertLess(s["gap_m"], 0.5)

    def test_scale_invariant(self):
        """The ratio must not change with image size -- panoramas and 2048px thumbs mix freely."""
        a, b = (0.10, 0.2, 0.12, 0.8), (0.20, 0.2, 0.22, 0.8)
        big = frame_separation(a, b)
        small = frame_separation(tuple(v / 2 for v in a), tuple(v / 2 for v in b))
        self.assertAlmostEqual(big["widths_apart"], small["widths_apart"], places=6)

    def test_ten_pole_widths_apart_is_about_three_metres(self):
        s = frame_separation((0.100, 0.2, 0.110, 0.8), (0.200, 0.2, 0.210, 0.8))
        self.assertAlmostEqual(s["widths_apart"], 10.0, places=6)
        self.assertAlmostEqual(s["gap_m"], 3.0, places=6)

    def test_degenerate_box_returns_none_rather_than_dividing_by_zero(self):
        self.assertIsNone(frame_separation((0.1, 0.2, 0.1, 0.8), (0.2, 0.2, 0.21, 0.8)))


class AdjudicateTest(unittest.TestCase):
    """The page's badge must never contradict the model's own reason. Both Marblehead misfires are
    pinned here."""

    def _r(self, **kw):
        base = {"is_double_pole": True, "other_pole_purpose": "utility", "poles_at_different_depths": False}
        return {**base, **kw}

    def test_pole_beside_a_streetlight_is_not_a_double(self):
        ok, why = adjudicate(self._r(other_pole_purpose="street_light"), None)
        self.assertFalse(ok)
        self.assertIn("streetlight".replace("light", " light"), why)

    def test_different_depths_is_not_a_double(self):
        ok, why = adjudicate(self._r(poles_at_different_depths=True), None)
        self.assertFalse(ok)
        self.assertIn("different distances", why)

    def test_a_real_double_survives(self):
        ok, why = adjudicate(self._r(), {"same_depth": True, "depth_ratio": 1.2})
        self.assertTrue(ok)
        self.assertIsNone(why)

    def test_geometry_never_rejects_on_its_own(self):
        # depth_ratio's median among real Marblehead doubles is 1.83, so it is reported as evidence
        # but must not gate -- a threshold tight enough to catch the one confirmed error also throws
        # out a third of the good calls.
        ok, _ = adjudicate(self._r(), {"same_depth": False, "depth_ratio": 5.0})
        self.assertTrue(ok)

    def test_a_pair_the_model_did_not_call_is_not_promoted(self):
        ok, why = adjudicate(self._r(is_double_pole=False), None)
        self.assertFalse(ok)
        self.assertIsNone(why)


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
