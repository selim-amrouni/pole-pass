"""Minimal Mapbox Vector Tile (MVT) decoder, stdlib only.

Decodes protobuf-encoded tiles into {layer_name: [feature, ...]} where each
feature is {"id": int|None, "type": "point"|"line"|"polygon", "props": dict,
"geom": [[(lon, lat), ...], ...]} with coordinates already projected to WGS84.
Only what the pole project needs; not a general MVT library.
"""
import gzip
import math
import zlib

# --- protobuf wire-format primitives -------------------------------------
def _varint(buf, i):
    result, shift = 0, 0
    while True:
        b = buf[i]; i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf):
    """Yield (field_number, wire_type, value) for a protobuf message."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        fnum, wtype = key >> 3, key & 7
        if wtype == 0:
            val, i = _varint(buf, i)
        elif wtype == 2:
            ln, i = _varint(buf, i)
            val = buf[i:i + ln]; i += ln
        elif wtype == 5:
            val = buf[i:i + 4]; i += 4
        elif wtype == 1:
            val = buf[i:i + 8]; i += 8
        else:
            raise ValueError(f"unsupported wire type {wtype}")
        yield fnum, wtype, val


def _zigzag(v):
    return (v >> 1) ^ -(v & 1)


def _packed_varints(buf):
    out, i = [], 0
    while i < len(buf):
        v, i = _varint(buf, i)
        out.append(v)
    return out


# --- MVT structure -------------------------------------------------------
def _value(buf):
    import struct
    for fnum, wtype, val in _fields(buf):
        if fnum == 1: return val.decode("utf-8", "replace")
        if fnum == 2: return struct.unpack("<f", val)[0]
        if fnum == 3: return struct.unpack("<d", val)[0]
        if fnum in (4, 5): return val
        if fnum == 6: return _zigzag(val)
        if fnum == 7: return bool(val)
    return None


def _geometry(cmds, gtype):
    """Decode MVT geometry commands into list of rings/lines of (x, y) tile coords."""
    parts, cur, x, y, i = [], [], 0, 0, 0
    while i < len(cmds):
        cmd_int = cmds[i]; i += 1
        cmd, count = cmd_int & 7, cmd_int >> 3
        if cmd == 1:  # MoveTo
            for _ in range(count):
                if cur: parts.append(cur); cur = []
                x += _zigzag(cmds[i]); y += _zigzag(cmds[i + 1]); i += 2
                cur.append((x, y))
        elif cmd == 2:  # LineTo
            for _ in range(count):
                x += _zigzag(cmds[i]); y += _zigzag(cmds[i + 1]); i += 2
                cur.append((x, y))
        elif cmd == 7:  # ClosePath
            if cur: cur.append(cur[0])
    if cur: parts.append(cur)
    return parts


def tile_to_lonlat(z, x, y, extent):
    """Return f(px, py) mapping tile-local ints to (lon, lat)."""
    n = 2 ** z
    def f(px, py):
        lon = (x + px / extent) / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n)))
        return lon, math.degrees(lat_rad)
    return f


def decode(data, z, x, y):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:1] == b"\x78":
        data = zlib.decompress(data)
    layers = {}
    for fnum, _, lbuf in _fields(data):
        if fnum != 3:
            continue
        name, extent, keys, values, feats = None, 4096, [], [], []
        for lf, _, lv in _fields(lbuf):
            if lf == 1: name = lv.decode()
            elif lf == 2: feats.append(lv)
            elif lf == 3: keys.append(lv.decode())
            elif lf == 4: values.append(_value(lv))
            elif lf == 5: extent = lv
        proj = tile_to_lonlat(z, x, y, extent)
        out = []
        for fb in feats:
            fid, gtype, tags, geom = None, 0, [], []
            for ff, _, fv in _fields(fb):
                if ff == 1: fid = fv
                elif ff == 2: tags = _packed_varints(fv)
                elif ff == 3: gtype = fv
                elif ff == 4: geom = _packed_varints(fv)
            props = {keys[tags[i]]: values[tags[i + 1]] for i in range(0, len(tags), 2)}
            parts = [[proj(px, py) for px, py in part] for part in _geometry(geom, gtype)]
            out.append({"id": fid, "type": {1: "point", 2: "line", 3: "polygon"}.get(gtype, "?"),
                        "props": props, "geom": parts})
        layers[name] = out
    return layers


def lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    xt = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    yt = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return xt, yt


def tiles_covering(bbox, z):
    w, s, e, n = bbox
    x0, y1 = lonlat_to_tile(w, s, z)
    x1, y0 = lonlat_to_tile(e, n, z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def decode_detection(b64):
    """Decode a Mapillary detection `geometry` (base64 MVT, layer 'mpy-or').

    Returns list of polygons, each a list of (x, y) normalized to 0..1 of the
    image width/height. Multiply by the thumbnail size to get pixels.
    """
    import base64
    data = base64.b64decode(b64)
    polys = []
    for fnum, _, lbuf in _fields(data):
        if fnum != 3:
            continue
        extent, feats = 4096, []
        for lf, _, lv in _fields(lbuf):
            if lf == 2: feats.append(lv)
            elif lf == 5: extent = lv
        for fb in feats:
            gtype, geom = 0, []
            for ff, _, fv in _fields(fb):
                if ff == 3: gtype = fv
                elif ff == 4: geom = _packed_varints(fv)
            for part in _geometry(geom, gtype):
                polys.append([(px / extent, py / extent) for px, py in part])
    return polys
