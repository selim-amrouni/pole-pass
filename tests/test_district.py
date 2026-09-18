"""district.py: the Voronoi cell that turns one village into an area the pipeline can run on.

Pure geometry only -- nothing here touches Overpass or the data/ tree.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geo
from district import bisector, cell, in_chosen
from dedupe import haversine_m

# A square "town" a little over 2 km on a side, near Newton's latitude so the local frame is
# representative. Wound counter-clockwise, as OSM outer rings are.
TOWN = [(-71.26, 42.32), (-71.24, 42.32), (-71.24, 42.34), (-71.26, 42.34), (-71.26, 42.32)]
WEST = ("West", -71.2575, 42.33)
EAST = ("East", -71.2425, 42.33)


class BisectorTest(unittest.TestCase):
    def test_line_is_equidistant_from_both_centres(self):
        a, b, _ = bisector(WEST[1:], EAST[1:])
        dw = abs(geo.signed_dist_to_line_m(WEST[1], WEST[2], a, b))
        de = abs(geo.signed_dist_to_line_m(EAST[1], EAST[2], a, b))
        self.assertAlmostEqual(dw, de, delta=1.0)  # metres

    def test_keep_side_contains_the_centre_it_belongs_to(self):
        a, b, keep_left = bisector(WEST[1:], EAST[1:])
        on_left = geo.signed_dist_to_line_m(WEST[1], WEST[2], a, b) > 0
        self.assertEqual(keep_left, on_left)

    def test_kept_side_excludes_the_other_centre(self):
        """Swapping the arguments reverses the line's direction AND which side each centre is on, so
        keep_left is expected to come back the same both ways. What must hold is that each call keeps
        its own centre and rejects the other."""
        for own, other in ((WEST, EAST), (EAST, WEST)):
            a, b, keep_left = bisector(own[1:], other[1:])
            side = lambda p: geo.signed_dist_to_line_m(p[1], p[2], a, b) > 0
            self.assertEqual(side(own), keep_left)
            self.assertNotEqual(side(other), keep_left)


class CellTest(unittest.TestCase):
    def test_cell_keeps_its_own_centre_and_drops_the_other(self):
        poly = cell(TOWN, WEST[1:], [WEST[1:], EAST[1:]])
        self.assertTrue(poly)
        self.assertTrue(geo.point_in_polygon(WEST[1], WEST[2], [poly]))
        self.assertFalse(geo.point_in_polygon(EAST[1], EAST[2], [poly]))

    def test_two_cells_split_the_town_roughly_in_half(self):
        w = geo.ring_area_m2(cell(TOWN, WEST[1:], [WEST[1:], EAST[1:]]))
        e = geo.ring_area_m2(cell(TOWN, EAST[1:], [WEST[1:], EAST[1:]]))
        whole = geo.ring_area_m2(TOWN)
        self.assertAlmostEqual(w + e, whole, delta=whole * 0.01)
        self.assertAlmostEqual(w, e, delta=whole * 0.02)

    def test_a_lone_centre_takes_the_whole_town(self):
        poly = cell(TOWN, WEST[1:], [WEST[1:]])
        self.assertAlmostEqual(geo.ring_area_m2(poly), geo.ring_area_m2(TOWN), delta=1.0)

    def test_cell_never_escapes_the_town_ring(self):
        poly = cell(TOWN, WEST[1:], [WEST[1:], EAST[1:]])
        for lon, lat in poly:
            self.assertTrue(-71.2601 <= lon <= -71.2399 and 42.3199 <= lat <= 42.3401)


class InChosenTest(unittest.TestCase):
    """The regression that matters: a village on the town line must not annex its neighbour.

    Newton Lower Falls sits on the Wellesley border. Points in Wellesley are nearer to its OSM node
    than to any Wellesley node, so a pure nearest-centre test pulled ~400 poles from another town
    into the district -- the same failure the repo already hit with Salem poles on a Marblehead page.
    """

    def setUp(self):
        self.centres = [WEST, EAST]
        self.bbox = (-71.26, 42.32, -71.24, 42.34)

    def test_point_inside_town_and_nearest_to_chosen_is_kept(self):
        self.assertTrue(in_chosen(-71.2575, 42.33, self.centres, {"West"}, TOWN, self.bbox))

    def test_point_inside_town_but_nearest_to_another_village_is_dropped(self):
        self.assertFalse(in_chosen(-71.2425, 42.33, self.centres, {"West"}, TOWN, self.bbox))

    def test_point_outside_the_town_is_dropped_even_when_nearest_to_chosen(self):
        outside = (-71.2650, 42.33)  # west of the town edge, still closest to West's node
        nearest = min(self.centres, key=lambda c: haversine_m(*outside, c[1], c[2]))[0]
        self.assertEqual(nearest, "West")          # the nearest-centre rule alone would keep it
        self.assertFalse(in_chosen(*outside, self.centres, {"West"}, TOWN, self.bbox))

    def test_bbox_reject_agrees_with_the_polygon_test(self):
        far = (-71.50, 42.90)
        self.assertFalse(in_chosen(*far, self.centres, {"West"}, TOWN, self.bbox))


if __name__ == "__main__":
    unittest.main()
