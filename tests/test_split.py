"""split.py boundary stitching and MMLD/VERIZON assignment on a synthetic ring, no network.

    uv run python3 -m unittest discover -s tests
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geo  # noqa: E402
from split import assign_halves, maintainer_of, stitch_ring  # noqa: E402

# A small rectangle standing in for the town boundary. Not a real place; sized like a real town
# (a bit over a km on a side) so the buffer/area maths below are the same order of magnitude as
# the real Marblehead run.
SW = (-71.00, 42.49)
SE = (-70.98, 42.49)
NE = (-70.98, 42.51)
NW = (-71.00, 42.51)
RING = [SW, SE, NE, NW, SW]


class StitchRingTest(unittest.TestCase):
    def test_shuffled_and_reversed_fragments_close_into_one_ring(self):
        # the four edges of RING, deliberately out of order and some reversed -- mirrors how
        # Overpass hands back a relation's 'outer' members with no guaranteed order or winding
        fragments = [
            [SE, NE],           # east edge, forward
            [NW, SW],           # west edge, forward
            list(reversed([NE, NW])),  # north edge, reversed
            list(reversed([SW, SE])),  # south edge, reversed
        ]
        ring = stitch_ring(fragments)
        self.assertEqual(ring[0], ring[-1], "stitched ring must close")
        self.assertEqual(set(ring[:-1]), {SW, SE, NE, NW})
        self.assertAlmostEqual(geo.ring_area_m2(ring), geo.ring_area_m2(RING), delta=1.0)

    def test_unmatched_fragment_raises(self):
        fragments = [[SE, NE], [NW, SW], [NE, NW]]  # missing the south edge entirely
        with self.assertRaises(ValueError):
            stitch_ring(fragments)


class AssignHalvesTest(unittest.TestCase):
    """Split RING along its own NW->SE diagonal: NE and SW corners then sit cleanly on
    opposite sides, and NW/SE themselves sit exactly on the line -- a fully worked-out case
    that doesn't depend on trusting the code under test to say which corner is which."""

    def setUp(self):
        self.line, self.mmld, self.verizon = assign_halves(NW, SE, RING)

    def test_halves_sum_to_whole_area(self):
        whole = geo.ring_area_m2(RING)
        split = geo.ring_area_m2(self.mmld) + geo.ring_area_m2(self.verizon)
        self.assertAlmostEqual(split, whole, delta=whole * 0.01)

    def test_ne_corner_is_mmld_sw_corner_is_verizon(self):
        # the whole point of the split: northeast is MMLD, southwest is Verizon, not the reverse
        self.assertEqual(maintainer_of(*NE, self.line, buffer_m=50), "MMLD")
        self.assertEqual(maintainer_of(*SW, self.line, buffer_m=50), "VERIZON")

    def test_uncertain_within_buffer_on_both_sides_of_the_line(self):
        # perpendicular offsets from the line's midpoint, oriented using the (already-confirmed)
        # NE corner so this test doesn't just restate assign_halves' own sign convention
        lat0 = (NW[1] + SE[1]) / 2
        fwd, inv = geo.local_frame(lat0)
        ax, ay = fwd(*NW)
        bx, by = fwd(*SE)
        mx, my = (ax + bx) / 2, (ay + by) / 2
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        perp = (-uy, ux)
        nex, ney = fwd(*NE)
        if perp[0] * (nex - mx) + perp[1] * (ney - my) < 0:
            perp = (-perp[0], -perp[1])  # now perp points toward the NE side

        def offset(dist_m):
            return inv(mx + perp[0] * dist_m, my + perp[1] * dist_m)

        for dist in (20, -20):  # a few metres either side of the line, well inside a 50 m buffer
            self.assertEqual(maintainer_of(*offset(dist), self.line, buffer_m=50), "UNCERTAIN")
        # far enough out (400 m > 50 m buffer) the correct label applies on each side
        self.assertEqual(maintainer_of(*offset(400), self.line, buffer_m=50), "MMLD")
        self.assertEqual(maintainer_of(*offset(-400), self.line, buffer_m=50), "VERIZON")

    def test_default_buffer_flags_points_on_the_line_itself(self):
        # NW and SE are themselves ON the split line (distance 0) -- always uncertain
        self.assertEqual(maintainer_of(*NW, self.line), "UNCERTAIN")
        self.assertEqual(maintainer_of(*SE, self.line), "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
