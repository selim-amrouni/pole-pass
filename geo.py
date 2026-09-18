"""Plain stdlib geometry helpers for the Marblehead maintenance-split work.

Lon/lat WGS84 in, metres out throughout. These are PLANAR APPROXIMATIONS valid
at town scale (a few km) -- this is not a projection library and none of this
is geodesically exact. `local_frame` gives an equirectangular metric plane
about a reference latitude (x scaled by cos(lat0)) so that "metres" in the
split maths -- clipping, side tests, densifying a road centreline -- is one
consistent thing; most functions below build on it with a local frame chosen
per call (e.g. a ring's own mean latitude) rather than one shared frame,
since callers pass geometry from different places. `to_merc`/`from_merc` are
the separate, fixed-origin spherical Web Mercator used for tile math; do not
mix them into the local-frame functions.

Reads nothing, writes nothing.
"""
import math

from dedupe import haversine_m

R_MERC = 6378137.0  # spherical Web Mercator radius, matches Mapillary/OSM tile math
M_PER_DEG_LAT = 111_320.0  # constant enough at town scale; see local_frame


def to_merc(lon, lat):
    """Spherical Web Mercator metres (R=6378137)."""
    x = math.radians(lon) * R_MERC
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R_MERC
    return x, y


def from_merc(x, y):
    lon = math.degrees(x / R_MERC)
    lat = math.degrees(2 * math.atan(math.exp(y / R_MERC)) - math.pi / 2)
    return lon, lat


def local_frame(lat0):
    """(fwd, inv) closures for an equirectangular local metric plane about latitude lat0.

    fwd(lon, lat) -> (x_m, y_m); inv(x_m, y_m) -> (lon, lat). x is scaled by
    cos(lat0) so x_m and y_m are the same "metre" near lat0. Distortion grows
    with distance from lat0 and from the reference meridian, which is fine at
    the town scale this project works at.
    """
    k = math.cos(math.radians(lat0)) * M_PER_DEG_LAT

    def fwd(lon, lat):
        return (lon * k, lat * M_PER_DEG_LAT)

    def inv(x_m, y_m):
        return (x_m / k, y_m / M_PER_DEG_LAT)

    return fwd, inv


def point_in_ring(lon, lat, ring):
    """Ray casting. ring is [(lon,lat), ...], closed or not."""
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at:
                inside = not inside
    return inside


def point_in_polygon(lon, lat, rings):
    """rings[0] is the outer ring, the rest are holes."""
    if not point_in_ring(lon, lat, rings[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in rings[1:])


def ring_area_m2(ring):
    """Absolute planar area, shoelace formula in a local frame about the ring's mean latitude."""
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    verts = pts[:-1]
    lat0 = sum(p[1] for p in verts) / len(verts)
    fwd, _ = local_frame(lat0)
    xy = [fwd(lon, lat) for lon, lat in pts]
    area2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:]))
    return abs(area2) / 2


def polyline_length_m(coords):
    return sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))


def densify(coords, step_m):
    """Points along a polyline at most step_m apart, always including both endpoints of every segment.

    Used to sample road centrelines before running seg_point_dist_m /
    signed_dist_to_line_m against each sample.
    """
    out = []
    for a, b in zip(coords, coords[1:]):
        d = haversine_m(*a, *b)
        n = max(1, math.ceil(d / step_m)) if d > 0 else 1
        for i in range(n):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    out.append(coords[-1])
    return out


def seg_point_dist_m(lon, lat, a, b):
    """Distance from (lon,lat) to the SEGMENT a-b (not the infinite line), clamped at the endpoints."""
    lat0 = (a[1] + b[1] + lat) / 3
    fwd, _ = local_frame(lat0)
    px, py = fwd(lon, lat)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def signed_dist_to_line_m(lon, lat, a, b):
    """Perpendicular distance to the INFINITE line through a and b; positive on the left of a->b.

    "Left" is standard map orientation (north up, east right): facing from a
    to b, positive is your left hand side. Used for the uncertainty buffer
    and side assignment in one call.
    """
    lat0 = (a[1] + b[1] + lat) / 3
    fwd, _ = local_frame(lat0)
    px, py = fwd(lon, lat)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return math.hypot(px - ax, py - ay)
    cross = dx * (py - ay) - dy * (px - ax)
    return cross / length


def clip_ring_to_halfplane(ring, a, b, keep_left):
    """Sutherland-Hodgman clip of ring against the half-plane bounded by the infinite line a->b.

    Keeps the left side (see signed_dist_to_line_m for the sign convention)
    when keep_left is true, the right side otherwise. Returns [] when nothing
    survives.

    KNOWN LIMITATION: if the half-plane cuts the subject ring into two
    disjoint pieces, Sutherland-Hodgman does not return two rings -- it
    returns one ring joined by a zero-width corridor along the cut. That is
    acceptable for this project's rough municipal split and must not be
    mistaken for a real multi-polygon clip.
    """
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    verts = pts[:-1]
    if not verts:
        return []
    lat0 = sum(p[1] for p in verts) / len(verts)
    fwd, inv = local_frame(lat0)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    dx, dy = bx - ax, by - ay

    def side(p):
        px, py = fwd(*p)
        cross = dx * (py - ay) - dy * (px - ax)
        return cross if keep_left else -cross

    def intersect(p1, p2):
        x1, y1 = fwd(*p1)
        x2, y2 = fwd(*p2)
        d1, d2 = side(p1), side(p2)
        t = d1 / (d1 - d2)
        return inv(x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    out = []
    n = len(verts)
    for i in range(n):
        cur, nxt = verts[i], verts[(i + 1) % n]
        cur_in, nxt_in = side(cur) >= 0, side(nxt) >= 0
        if cur_in:
            out.append(cur)
            if not nxt_in:
                out.append(intersect(cur, nxt))
        elif nxt_in:
            out.append(intersect(cur, nxt))
    return out


def line_polygon_exits(a, b, ring):
    """Every intersection of the INFINITE line through a,b with ring's edges, sorted along a->b.

    Used to extend a hand-drawn segment out to the town boundary. A ring
    vertex that sits exactly on the line is reported once, not twice from its
    two adjacent edges; two crossings that land within a millimetre of each
    other collapse to one (guards against a strict-crossing test and an
    exact-vertex test both firing near the same point).
    """
    pts = ring if ring[0] == ring[-1] else ring + [ring[0]]
    verts = pts[:-1]
    if len(verts) < 2:
        return []
    lat0 = sum(p[1] for p in verts) / len(verts)
    fwd, inv = local_frame(lat0)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    dx, dy = bx - ax, by - ay

    def side(p):
        px, py = fwd(*p)
        return dx * (py - ay) - dy * (px - ax)

    n = len(verts)
    hits = []
    for i in range(n):
        p1, p2 = verts[i], verts[(i + 1) % n]
        s1, s2 = side(p1), side(p2)
        if s1 == 0:
            hits.append(p1)
        if s1 * s2 < 0:
            x1, y1 = fwd(*p1)
            x2, y2 = fwd(*p2)
            t = s1 / (s1 - s2)
            hits.append(inv(x1 + t * (x2 - x1), y1 + t * (y2 - y1)))

    def mpos(h):
        hx, hy = fwd(*h)
        return hx, hy

    uniq = []
    for h in hits:
        hx, hy = mpos(h)
        if not any(math.hypot(hx - mpos(u)[0], hy - mpos(u)[1]) < 1e-3 for u in uniq):
            uniq.append(h)

    def param(h):
        hx, hy = mpos(h)
        return dx * (hx - ax) + dy * (hy - ay)

    return sorted(uniq, key=param)
