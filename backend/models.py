"""Plain-dict document shapes for MongoDB (no ORM) + small helpers.

Collections (see database.py):
  settings  - one document, _id="singleton": {overhead_threshold_m, line_threshold_m, address_tolerance_m}
  uploads   - {_id, filename, uploaded_at, row_count, has_line_distance, has_address}
  fdps      - {_id, upload_id, ext_id, lat, lon, is_suitable, rejection_reason}
  customers - {_id, upload_id, fdp_id, ext_id, name, address, lat, lon, line_distance_m,
               is_suitable, rejection_reason,
               geocode_status, geo_lat, geo_lon, geo_display_name, geo_distance_m,
               geo_road_name, geo_road_status, geo_road_lat, geo_road_lon, geo_road_distance_m}
               (the geo_road_* fields are the "top down" road-level lookup: the road the
               full address resolved to, re-geocoded on its own - see geocoding.py. The
               address-match pass/fail check uses geo_road_distance_m when available,
               falling back to geo_distance_m otherwise.)

`upload_id`/`fdp_id` are stored as strings (str(ObjectId)) rather than ObjectId
references, which keeps every id in the API and frontend a plain string.
"""
from bson import ObjectId


def oid(id_str):
    """str -> ObjectId, raising a clear error on a malformed id."""
    try:
        return ObjectId(id_str)
    except Exception:
        raise ValueError(f"Not a valid id: {id_str!r}")


def serialize(doc, extra_drop=()):
    """Mongo document -> plain dict with '_id' replaced by a string 'id'."""
    if doc is None:
        return None
    out = dict(doc)
    out["id"] = str(out.pop("_id"))
    for key in extra_drop:
        out.pop(key, None)
    return out
