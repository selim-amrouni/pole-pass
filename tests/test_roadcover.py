"""roadcover.py helpers: highway filtering, length-weighted coverage, quarter bucketing. No network.

    uv run python3 -m unittest discover -s tests
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geo  # noqa: E402
from roadcover import (  # noqa: E402
    STREET_CELL_M, STREET_MAX_M, coverage_share, kept_ways, nearest_street, quarter_of,
    street_index, street_rows, streets_with_no_coverage,
)


def way(id_, highway, name=None, area=None, coords=None):
    tags = {"highway": highway}
    if name:
        tags["name"] = name
    if area:
        tags["area"] = area
    geom = coords or [{"lon": 0.0, "lat": 0.0}, {"lon": 0.001, "lat": 0.0}]
    return {"type": "way", "id": id_, "tags": tags, "geometry": geom}


class KeptWaysTest(unittest.TestCase):
    def test_keeps_residential_drops_service_and_footway(self):
        raw = {"elements": [way(1, "residential"), way(2, "service"), way(3, "footway")]}
        self.assertEqual([w["id"] for w in kept_ways(raw)], [1])

    def test_drops_area_yes(self):
        raw = {"elements": [way(1, "residential", area="yes"), way(2, "residential")]}
        self.assertEqual([w["id"] for w in kept_ways(raw)], [2])

    def test_keeps_link_variants_and_unclassified(self):
        raw = {"elements": [way(1, "primary_link"), way(2, "unclassified"), way(3, "track")]}
        self.assertEqual({w["id"] for w in kept_ways(raw)}, {1, 2})

    def test_drops_ways_with_fewer_than_two_coords(self):
        raw = {"elements": [way(1, "residential", coords=[{"lon": 0.0, "lat": 0.0}])]}
        self.assertEqual(kept_ways(raw), [])

    def test_ignores_non_way_elements(self):
        raw = {"elements": [{"type": "node", "id": 1, "tags": {"highway": "residential"}}]}
        self.assertEqual(kept_ways(raw), [])


class CoverageShareTest(unittest.TestCase):
    def test_weighted_by_length_not_by_way_count(self):
        # a 1 km street entirely uncovered (100 samples @ 10 m) and a 10 m street fully covered
        # (1 sample): a per-way average would say 50%; length-weighted should land near 1%.
        long_uncovered = [{"id": f"long:{i}"} for i in range(100)]
        short_covered = [{"id": "short:0"}]
        samples = long_uncovered + short_covered
        covered = {"short:0": True}
        share = coverage_share(samples, covered)
        self.assertAlmostEqual(share, 1 / 101, delta=0.005)
        self.assertLess(share, 0.10)  # nowhere near the 50% a per-way average would give

    def test_empty_is_zero(self):
        self.assertEqual(coverage_share([], {}), 0.0)

    def test_fully_covered_is_one(self):
        samples = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(coverage_share(samples, {"a": True, "b": True}), 1.0)


class StreetRowsTest(unittest.TestCase):
    def test_zero_coverage_street_appears_in_no_coverage_list(self):
        samples = [
            {"id": "a:0", "name": "Elm Street"}, {"id": "a:1", "name": "Elm Street"},
            {"id": "b:0", "name": "Oak Street"},
        ]
        covered = {"a:0": True, "a:1": True, "b:0": False}
        rows = street_rows(samples, covered, step_m=10.0)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Elm Street"]["covered_fraction"], 1.0)
        self.assertEqual(by_name["Oak Street"]["covered_fraction"], 0.0)
        self.assertEqual(by_name["Oak Street"]["length_m"], 10.0)
        no_cov = streets_with_no_coverage(rows)
        self.assertEqual([r["name"] for r in no_cov], ["Oak Street"])

    def test_longest_first_and_unnamed_excluded(self):
        samples = [
            {"id": "a:0", "name": None}, {"id": "a:1", "name": None},  # unnamed, excluded
            {"id": "b:0", "name": "Short Lane"},
            {"id": "c:0", "name": "Long Road"}, {"id": "c:1", "name": "Long Road"}, {"id": "c:2", "name": "Long Road"},
        ]
        covered = {}
        rows = street_rows(samples, covered, step_m=10.0)
        self.assertEqual({r["name"] for r in rows}, {"Short Lane", "Long Road"})
        no_cov = streets_with_no_coverage(rows)
        self.assertEqual([r["name"] for r in no_cov], ["Long Road", "Short Lane"])

    def test_way_segments_sharing_a_name_aggregate_into_one_row(self):
        # a street cut into two OSM ways at an intersection still reports as one row
        samples = [{"id": "a:0", "name": "Main Street"}, {"id": "b:0", "name": "Main Street"}]
        rows = street_rows(samples, {"a:0": True}, step_m=10.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n_samples"], 2)
        self.assertEqual(rows[0]["covered_fraction"], 0.5)


class QuarterOfTest(unittest.TestCase):
    def test_known_timestamp_lands_in_right_quarter(self):
        ms = int(datetime(2023, 5, 15, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertEqual(quarter_of(ms), "2023-Q2")

    def test_quarter_boundaries(self):
        self.assertEqual(quarter_of(int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)), "2024-Q1")
        self.assertEqual(quarter_of(int(datetime(2024, 3, 31, 23, 59, tzinfo=timezone.utc).timestamp() * 1000)), "2024-Q1")
        self.assertEqual(quarter_of(int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)), "2024-Q2")
        self.assertEqual(quarter_of(int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)), "2024-Q4")


if __name__ == "__main__":
    unittest.main()


class StreetIndexTest(unittest.TestCase):
    """The grid index must return exactly what a full scan would; it only exists to be faster."""

    @staticmethod
    def _scan(lon, lat, ways):
        """Reference answer: every segment of every named way, no index. Deliberately naive."""
        best_name, best_d = None, None
        for w in ways:
            if not w.get("name"):
                continue
            for a, b in zip(w["coords"], w["coords"][1:]):
                d = geo.seg_point_dist_m(lon, lat, a, b)
                if best_d is None or d < best_d:
                    best_name, best_d = w["name"], d
        return best_name, best_d

    @classmethod
    def _unique_winner(cls, lon, lat, ways, best_d):
        """True when exactly one named way achieves the minimum distance (no tie to break)."""
        at_min = 0
        for w in ways:
            if not w.get("name"):
                continue
            d = min(geo.seg_point_dist_m(lon, lat, a, b) for a, b in zip(w["coords"], w["coords"][1:]))
            if abs(d - best_d) < 1e-9:
                at_min += 1
        return at_min == 1

    @staticmethod
    def _grid(lat0=42.5, lon0=-71.1, n=9):
        """A lattice of named streets, so a point can sit near several of them at once."""
        ways = []
        for i in range(n):
            ways.append({"id": i, "name": f"H{i}", "coords": [(lon0 + j * 0.001, lat0 + i * 0.001) for j in range(n)]})
            ways.append({"id": 100 + i, "name": f"V{i}", "coords": [(lon0 + i * 0.001, lat0 + j * 0.001) for j in range(n)]})
        return ways

    def test_matches_a_full_scan_everywhere_on_the_lattice(self):
        ways = self._grid()
        idx = street_index(ways)
        for a in range(17):
            for b in range(17):
                lon, lat = -71.1 + a * 0.0005, 42.5 + b * 0.0005
                got_name, got_d = nearest_street(idx, lon, lat)
                want_name, want_d = self._scan(lon, lat, ways)
                if want_d is not None and want_d > STREET_MAX_M:
                    self.assertIsNone(got_name, f"beyond range at {lon},{lat}")
                    continue
                # The distance is the invariant. On a lattice a point can sit exactly between a
                # horizontal and a vertical street; both names are then equally correct and the two
                # iteration orders disagree, so only pin the name where the winner is unique.
                self.assertAlmostEqual(got_d, want_d, places=6, msg=f"at {lon},{lat}")
                if self._unique_winner(lon, lat, ways, want_d):
                    self.assertEqual(got_name, want_name, f"at {lon},{lat}")

    def test_works_across_the_meridian_the_equator_and_the_southern_hemisphere(self):
        # int() truncates toward zero, so negative coordinates are the case a grid index gets wrong.
        for lon0, lat0 in [(-0.002, 0.0), (0.0, -0.002), (-0.002, -0.002), (179.99, 51.5)]:
            ways = self._grid(lat0=lat0, lon0=lon0, n=5)
            idx = street_index(ways)
            for a in range(9):
                for b in range(9):
                    lon, lat = lon0 + a * 0.0005, lat0 + b * 0.0005
                    got_name, got_d = nearest_street(idx, lon, lat)
                    want_name, want_d = self._scan(lon, lat, ways)
                    if want_d is None or want_d > STREET_MAX_M:
                        self.assertIsNone(got_name, f"at {lon},{lat} near ({lon0},{lat0})")
                        continue
                    self.assertAlmostEqual(got_d, want_d, places=6, msg=f"at {lon},{lat} near ({lon0},{lat0})")
                    if self._unique_winner(lon, lat, ways, want_d):
                        self.assertEqual(got_name, want_name, f"at {lon},{lat} near ({lon0},{lat0})")

    def test_unnamed_ways_are_ignored_and_empty_input_is_harmless(self):
        idx = street_index([{"id": 1, "name": None, "coords": [(-71.1, 42.5), (-71.09, 42.5)]}])
        self.assertEqual(nearest_street(idx, -71.1, 42.5), (None, None))
        self.assertEqual(nearest_street(street_index([]), -71.1, 42.5), (None, None))
        self.assertEqual(nearest_street(None, -71.1, 42.5), (None, None))

    def test_nothing_is_claimed_beyond_the_range(self):
        ways = [{"id": 1, "name": "Far Street", "coords": [(-71.1, 42.5), (-71.09, 42.5)]}]
        idx = street_index(ways)
        name, d = nearest_street(idx, -71.1, 42.5001)
        self.assertEqual(name, "Far Street")
        self.assertLess(d, STREET_MAX_M)
        self.assertEqual(nearest_street(idx, -71.1, 42.5100), (None, None), "600 m away: no claim")

    def test_a_radius_past_the_cell_size_is_refused_rather_than_answered_wrongly(self):
        # Only the 3x3 neighbourhood is searched, so a bigger radius would miss segments in silence.
        idx = street_index([{"id": 1, "name": "A", "coords": [(-71.1, 42.5), (-71.09, 42.5)]}])
        with self.assertRaises(ValueError):
            nearest_street(idx, -71.1, 42.5, max_m=STREET_CELL_M + 1)
