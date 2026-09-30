"""Land/water/Sri-Lanka suitability check.

A point is REJECTED if either:
  - it falls outside Sri Lanka's land boundary (i.e. in the sea, or in
    another country), or
  - it falls inside one of the known major lakes/reservoirs/lagoons listed
    in backend/geodata/water_bodies.json.

This is intentionally a coarse, approximate check (see backend/geodata/water_bodies.json
for caveats) - it will not catch every small pond, tank or river, only the
big/obvious mistakes: a point plotted in the ocean, well outside the island,
or inside one of Sri Lanka's largest reservoirs/lagoons.
"""
import json
import math

from shapely.geometry import shape, Point

from . import config

_boundary_polygon = None
_water_bodies = None


def _load():
    global _boundary_polygon, _water_bodies
    if _boundary_polygon is None:
        with open(config.GEODATA_DIR / "sri_lanka_boundary.geojson") as f:
            gj = json.load(f)
        _boundary_polygon = shape(gj["features"][0]["geometry"])
    if _water_bodies is None:
        with open(config.GEODATA_DIR / "water_bodies.json") as f:
            _water_bodies = json.load(f)["bodies"]
    return _boundary_polygon, _water_bodies


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def check_point(lat, lon):
    """Returns (is_suitable: bool, reason: str | None)."""
    if lat is None or lon is None:
        return False, "Missing coordinates"

    boundary, water_bodies = _load()
    point = Point(lon, lat)  # shapely uses (x=lon, y=lat)

    if not boundary.contains(point):
        return False, "Outside Sri Lanka"

    for body in water_bodies:
        d = _haversine_m(lat, lon, body["lat"], body["lon"])
        if d <= body["radius_m"]:
            return False, f"On/near {body['name']} (known water body)"

    return True, None
