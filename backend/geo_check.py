"""Sri Lanka suitability check.

A point is REJECTED if it is missing, or if it falls outside Sri Lanka's
land boundary (in the sea, or in another country).

The boundary comes from backend/geodata/sri_lanka_boundary.geojson.
"""
import json

from shapely.geometry import shape, Point

from . import config

_boundary_polygon = None


def _load():
    global _boundary_polygon
    if _boundary_polygon is None:
        with open(config.GEODATA_DIR / "sri_lanka_boundary.geojson") as f:
            gj = json.load(f)
        _boundary_polygon = shape(gj["features"][0]["geometry"])
    return _boundary_polygon


def check_point(lat, lon):
    """Returns (is_suitable: bool, reason: str | None)."""
    if lat is None or lon is None:
        return False, "Missing coordinates"

    boundary = _load()
    point = Point(lon, lat)  # shapely uses (x=lon, y=lat)

    if not boundary.contains(point):
        return False, "Outside Sri Lanka"

    return True, None