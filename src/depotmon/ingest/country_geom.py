"""Country lookup by real polygon, not by bounding box.

Why this replaced the bbox test: a bounding box is a rectangle, and Europe is
not. Aachen (6.1 E, 50.9 N) sits inside the Dutch AND the German AND the
Belgian rectangle, and a bbox test labelled it BE. That is a wrong country on
the map, which is worse than no country at all.

The polygons come from the `datasets/geo-countries` GeoJSON, which carries an
ISO-3166 alpha-2 code per country. The file is 14 MB, so it is read once, kept
in memory, and the polygons are reduced to their outer rings.

Lookup is a point-in-polygon ray cast, exactly like the Dutch province lookup
in scripts/export_webapp_data.py. No extra dependency, no service call, and it
works offline.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

GEOJSON = pathlib.Path(__file__).resolve().parents[3] / "resources" / "geo" / "countries-europe.geojson"

# Countries the map layer accepts as "in Europe".
EUROPE_CODES = frozenset({
    "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE",
    "FI", "FR", "DE", "GR", "HU", "IS", "IE", "IT", "XK", "LV", "LI", "LT",
    "LU", "MT", "MD", "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RU",
    "SM", "RS", "SK", "SI", "ES", "SE", "CH", "UA", "GB", "VA", "TR",
})


def _in_ring(x: float, y: float, ring: list) -> bool:
    """Ray casting. The same test the Dutch province lookup uses."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_geom(x: float, y: float, geom: dict) -> bool:
    """True when the point is in the polygon, and not in one of its holes."""
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return False
    for poly in polys:
        if not poly:
            continue
        if not _in_ring(x, y, poly[0]):
            continue
        if any(_in_ring(x, y, hole) for hole in poly[1:]):
            continue
        return True
    return False


@lru_cache(maxsize=1)
def _geometries() -> dict:
    """country code -> geometry, for the European countries only."""
    if not GEOJSON.exists():
        return {}
    data = json.loads(GEOJSON.read_text())
    out: dict[str, dict] = {}
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        code = (props.get("ISO3166-1-Alpha-2") or "").strip().upper()
        geom = feat.get("geometry")
        if code and geom and code in EUROPE_CODES:
            out[code] = geom
    return out


@lru_cache(maxsize=1)
def _areas() -> dict:
    """Absolute polygon area per country, in square degrees.

    Used to break ties: when two countries claim the same point (a disputed
    area, or a border that both datasets carry), the smaller one wins. A
    microstate on a border is the better answer than its large neighbour.
    """
    out: dict[str, float] = {}
    for code, geom in _geometries().items():
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        area = 0.0
        for poly in polys:
            if not poly:
                continue
            ring = poly[0]
            acc = 0.0
            for i in range(len(ring)):
                x1, y1 = ring[i][0], ring[i][1]
                x2, y2 = ring[(i + 1) % len(ring)][0], ring[(i + 1) % len(ring)][1]
                acc += x1 * y2 - x2 * y1
            area += abs(acc) / 2.0
        out[code] = area
    return out


_BBOXES: dict | None = None


def _bbox_of(geom) -> tuple:
    """Bounding box of a (Multi)Polygon geometry, as (minx, miny, maxx, maxy)."""
    xs: list[float] = []
    ys: list[float] = []
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    else:
        polys = geom["coordinates"]
    for poly in polys:
        for ring in poly:
            for pt in ring:
                xs.append(pt[0])
                ys.append(pt[1])
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _bboxes() -> dict:
    global _BBOXES
    if _BBOXES is None:
        _BBOXES = {code: _bbox_of(geom) for code, geom in _geometries().items()}
    return _BBOXES


def country_for(lon: float, lat: float) -> str:
    """ISO-2 country for a point, or an empty string outside Europe."""
    x, y = float(lon), float(lat)
    best = ""
    best_area = None
    boxes = _bboxes()
    for code, geom in _geometries().items():
        # Cheap rejection first. A point outside a country's bounding box is
        # certainly outside the country, so the polygon walk can be skipped.
        # The box never ACCEPTS a point; it only rejects, so the answer is
        # unchanged.
        box = boxes.get(code)
        if box is not None and not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            continue
        if not _point_in_geom(x, y, geom):
            continue
        area = _areas().get(code, 0.0)
        if best_area is None or area < best_area:
            best, best_area = code, area
    return best


def is_available() -> bool:
    """True when the polygon file is present and readable."""
    return GEOJSON.exists() and bool(_geometries())
