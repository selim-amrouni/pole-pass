"""geo.py planar-approximation helpers, no network.

    uv run python3 -m unittest discover -s tests
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geo import (  # noqa: E402
    clip_ring_to_halfplane, densify, from_merc, line_polygon_exits, local_frame,
    point_in_polygon, point_in_ring, polyline_length_m, ring_area_m2,
    seg_point_dist_m, signed_dist_to_line_m, to_merc,
)

LAT0 = 42.50  # Marblehead-ish reference latitude

# L-shaped (concave) ring: a 2x2 square with the top-right 1x1 quadrant removed.
L_RING = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]

# axis-aligned unit square, used for area and clip checks
SQUARE = [(0, 0), (1, 0), (1, 1), (0, 1)]


class MercTest(unittest.TestCase):
    def test_round_trip(self):
        for lon, lat in [(0, 0), (-71.05, 42.50), (139.7, 35.7), (-179.9, -60.0)]:
            x, y = to_merc(lon, lat)
            lon2, lat2 = from_merc(x, y)
            self.assertAlmostEqual(lon, lon2, delta=1e-9)
            self.assertAlmostEqual(lat, lat2, delta=1e-9)


class PointInRingTest(unittest.TestCase):
    def test_inside_concave_ring(self):
        self.assertTrue(point_in_ring(0.5, 0.5, L_RING))  # inside the "leg" of the L

    def test_outside_in_the_notch(self):
        self.assertFalse(point_in_ring(1.5, 1.5, L_RING))  # inside the removed quadrant

    def test_outside_far_away(self):
        self.assertFalse(point_in_ring(10, 10, L_RING))

    def test_near_a_vertex_of_the_notch(self):
        # just inside the leg, adjacent to the reflex vertex at (1,1)
        self.assertTrue(point_in_ring(0.99, 0.99, L_RING))
        # just outside, on the notch side of the same vertex
        self.assertFalse(point_in_ring(1.01, 1.01, L_RING))

    def test_point_in_polygon_hole(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
        self.assertTrue(point_in_polygon(1, 1, [outer, hole]))
        self.assertFalse(point_in_polygon(5, 5, [outer, hole]))  # inside the hole
        self.assertFalse(point_in_polygon(20, 20, [outer, hole]))  # outside entirely


class DensifyTest(unittest.TestCase):
    def test_respects_step_and_keeps_endpoints(self):
        fwd, _ = local_frame(LAT0)
        a, b = (-71.05, 42.50), (-71.04, 42.50)
        pts = densify([a, b], 20.0)
        self.assertEqual(pts[0], a)
        self.assertEqual(pts[-1], b)
        for p, q in zip(pts, pts[1:]):
            px, py = fwd(*p)
            qx, qy = fwd(*q)
            # densify sizes steps off haversine distance; checking with the local-frame metric
            # here means a little slack for the two approximations disagreeing, not a bug.
            self.assertLessEqual(math.hypot(qx - px, qy - py), 20.0 * 1.01)

    def test_multi_segment_keeps_every_vertex(self):
        coords = [(0, 0), (0.001, 0), (0.001, 0.001)]
        pts = densify(coords, 5.0)
        for v in coords:
            self.assertIn(v, pts)


class SegPointDistTest(unittest.TestCase):
    def test_clamped_beyond_endpoints(self):
        a, b = (0.0, LAT0), (0.01, LAT0)  # short east-west segment
        # a point due east, well past b, should measure to b, not the infinite line
        d_past_b = seg_point_dist_m(0.02, LAT0, a, b)
        fwd, _ = local_frame(LAT0)
        bx, by = fwd(*b)
        px, py = fwd(0.02, LAT0)
        self.assertAlmostEqual(d_past_b, math.hypot(px - bx, py - by), delta=0.5)

        d_past_a = seg_point_dist_m(-0.01, LAT0, a, b)
        ax, ay = fwd(*a)
        px, py = fwd(-0.01, LAT0)
        self.assertAlmostEqual(d_past_a, math.hypot(px - ax, py - ay), delta=0.5)

    def test_on_segment_is_near_zero(self):
        a, b = (0.0, LAT0), (0.01, LAT0)
        self.assertAlmostEqual(seg_point_dist_m(0.005, LAT0, a, b), 0.0, delta=0.5)


class SignedDistTest(unittest.TestCase):
    def test_sign_flips_and_magnitude_east_west_line(self):
        a, b = (0.0, LAT0), (0.01, LAT0)  # heading east
        fwd, _ = local_frame(LAT0)
        _, ay = fwd(*a)
        step_deg = 0.0005  # a bit north
        north = signed_dist_to_line_m(0.005, LAT0 + step_deg, a, b)
        south = signed_dist_to_line_m(0.005, LAT0 - step_deg, a, b)
        self.assertGreater(north, 0)  # north is left of an eastward heading
        self.assertLess(south, 0)
        _, py = fwd(0.005, LAT0 + step_deg)
        expected = py - ay
        self.assertAlmostEqual(north, expected, delta=abs(expected) * 0.01)
        self.assertAlmostEqual(south, -north, delta=abs(expected) * 0.01)


class ClipRingTest(unittest.TestCase):
    def test_halves_sum_to_whole_area(self):
        whole = ring_area_m2(SQUARE)
        a, b = (0.5, -1.0), (0.5, 2.0)  # vertical line through the middle of the square
        left = clip_ring_to_halfplane(SQUARE, a, b, True)
        right = clip_ring_to_halfplane(SQUARE, a, b, False)
        area_left = ring_area_m2(left) if left else 0.0
        area_right = ring_area_m2(right) if right else 0.0
        self.assertAlmostEqual(area_left + area_right, whole, delta=whole * 0.01)
        self.assertAlmostEqual(area_left, whole / 2, delta=whole * 0.01)

    def test_fully_outside_returns_empty(self):
        a, b = (5.0, -1.0), (5.0, 2.0)  # line entirely east of the square, heading north
        # heading north, "left" is west -- the square is on the left, so keep_left=False empties it
        self.assertEqual(clip_ring_to_halfplane(SQUARE, a, b, False), [])


class LinePolygonExitsTest(unittest.TestCase):
    def test_two_ordered_crossings_on_convex_ring(self):
        ring = [(0, 0), (10, 0), (10, 10), (0, 10)]  # convex square
        a, b = (-5, 5), (15, 5)  # west-to-east line through the middle
        hits = line_polygon_exits(a, b, ring)
        self.assertEqual(len(hits), 2)
        self.assertAlmostEqual(hits[0][0], 0, delta=1e-6)
        self.assertAlmostEqual(hits[1][0], 10, delta=1e-6)  # ordered along a->b (west to east)

    def test_no_crossing_returns_empty(self):
        ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
        a, b = (20, -5), (20, 15)  # line entirely east of the ring
        self.assertEqual(line_polygon_exits(a, b, ring), [])


class PolylineLengthTest(unittest.TestCase):
    def test_sums_segments(self):
        a, b, c = (0.0, LAT0), (0.01, LAT0), (0.01, LAT0 + 0.01)
        from dedupe import haversine_m
        expected = haversine_m(*a, *b) + haversine_m(*b, *c)
        self.assertAlmostEqual(polyline_length_m([a, b, c]), expected, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
