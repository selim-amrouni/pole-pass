"""osm.py matching on synthetic points, no network.

    uv run python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osm import HEADLINE_M, diff, nearest_within, osm_nodes, overpass_query  # noqa: E402

LAT, LON = 44.52, -72.30
M_LAT = 1 / 111_320  # degrees per meter of latitude at any latitude


def at(dn_m, de_m=0.0):
    """(lon, lat) dn_m meters north and de_m meters east of the anchor."""
    import math
    return (LON + de_m * M_LAT / math.cos(math.radians(LAT)), LAT + dn_m * M_LAT)


class NearestTest(unittest.TestCase):
    def test_nearest_within_picks_closest_and_respects_radius(self):
        targets = [("a", *at(0), "power=pole"), ("b", *at(6), "power=pole"), ("c", *at(40), "power=pole")]
        got = nearest_within([("p", *at(4))], targets, 25)
        self.assertEqual(got[0][1], "b"); self.assertAlmostEqual(got[0][2], 2.0, delta=0.2)
        self.assertEqual(nearest_within([("q", *at(80))], targets, 25), [("q", None, None)])
        self.assertEqual(nearest_within([("r", *at(0))], [], 25), [("r", None, None)])

    def test_grid_does_not_lose_neighbours_across_cell_edges(self):
        # a target just across a cell boundary (CELL_DEG ~55 m) must still be found within 25 m
        pts = [("p", *at(54.9))]; targets = [("t", *at(56.0), "power=pole")]
        self.assertEqual(nearest_within(pts, targets, 25)[0][1], "t")


class HighLatitudeSweepTest(unittest.TestCase):
    def test_no_misses_across_cell_edges_at_high_latitude(self):
        # Fairbanks-like: |lon| large, cos(lat) small. Sweep points 24 m from a target in 12 bearings across many positions.
        import math
        lat0, lon0 = 64.8, -147.7
        m_lat = 1 / 111_320; m_lon = m_lat / math.cos(math.radians(lat0))
        misses = 0
        for i in range(400):
            tl = (lon0 + i * 0.00013, lat0 + i * 0.00011)
            for b in range(12):
                a = math.radians(b * 30)
                p = (tl[0] + 24 * math.sin(a) * m_lon, tl[1] + 24 * math.cos(a) * m_lat)
                got = nearest_within([("p", *p)], [("t", *tl, "power=pole")], 25)
                misses += got[0][1] is None
        self.assertEqual(misses, 0)


class DiffTest(unittest.TestCase):
    def test_counts_add_up(self):
        poles = [("p1", *at(0)), ("p2", *at(100)), ("p3", *at(200)), ("p4", *at(210))]
        nodes = [("n1", *at(3), "power=pole"), ("n2", *at(300), "man_made=utility_pole"), ("n3", *at(220), "power=pole")]
        matches, s = diff(poles, nodes)
        self.assertEqual(s["detected_utility"], 4)
        self.assertEqual(s["osm_nodes_by_tag"], {"power=pole": 2, "man_made=utility_pole": 1})
        self.assertEqual(s["detected_with_osm_within_m"], {"8": 1, "15": 2, "25": 3})
        self.assertEqual(s["detected_without_osm_within_headline"] + s["detected_with_osm_within_m"][str(HEADLINE_M)], 4)
        self.assertEqual(s["osm_without_detected_within_headline"], 1, "n2 at 300 m north has no pole")
        by_id = {m["pole_id"]: m for m in matches}
        self.assertEqual(by_id["p1"]["osm_id"], "n1"); self.assertEqual(by_id["p2"]["osm_id"], None)
        self.assertEqual(by_id["p4"]["osm_tag"], "power=pole")

    def test_osm_nodes_keeps_only_pole_tags(self):
        raw = {"elements": [{"type": "node", "id": 1, "lat": LAT, "lon": LON, "tags": {"power": "pole"}},
                            {"type": "node", "id": 2, "lat": LAT, "lon": LON, "tags": {"man_made": "utility_pole"}},
                            {"type": "node", "id": 3, "lat": LAT, "lon": LON, "tags": {"power": "tower"}},
                            {"type": "way", "id": 4, "tags": {"power": "pole"}}]}
        self.assertEqual([n[0] for n in osm_nodes(raw)], [1, 2])

    def test_query_uses_south_west_north_east(self):
        self.assertIn("(40.71,-73.96,40.74,-73.93)", overpass_query([-73.96, 40.71, -73.93, 40.74]))


if __name__ == "__main__":
    unittest.main()
