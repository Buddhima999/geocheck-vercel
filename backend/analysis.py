"""Distance math and pass/fail logic shared by both pages.

Works on plain dicts (as read back from MongoDB) rather than ORM objects.
Nothing here is persisted except what's expensive to recompute (suitability,
geocoding) - overhead/line-distance pass/fail is derived live from the stored
coordinates plus whatever thresholds are currently in Settings, so dragging a
threshold slider updates results immediately without re-uploading.
"""
import math


def haversine_m(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def overhead_check_dict(customer, fdp, threshold_m):
    """Straight-line ('as the crow flies') distance from customer to FDP."""
    if fdp is None or customer.get("lat") is None or customer.get("lon") is None or fdp.get("lat") is None or fdp.get("lon") is None:
        return {"applicable": False, "distance_m": None, "threshold_m": threshold_m, "passed": None}
    d = haversine_m(customer["lat"], customer["lon"], fdp["lat"], fdp["lon"])
    return {"applicable": True, "distance_m": round(d, 1), "threshold_m": threshold_m, "passed": d <= threshold_m}


def line_distance_check_dict(customer, threshold_m):
    """Uses the optional CSV 'line distance' column (actual routed cable length)."""
    d = customer.get("line_distance_m")
    if d is None:
        return {"applicable": False, "distance_m": None, "threshold_m": threshold_m, "passed": None}
    return {"applicable": True, "distance_m": round(d, 1), "threshold_m": threshold_m, "passed": d <= threshold_m}


def address_match_check_dict(customer, tolerance_m):
    """Top-down address check: is the customer within tolerance of the ROAD
    their address resolves to (not the exact rooftop point)?

    Rooftop-level geocoding (free or paid) is often off by a house or two, so
    pass/fail is based on distance to the address's road when that lookup
    succeeded (basis="road") - that's what's shown on the map. It falls back
    to the plain address point (basis="address") if a road name couldn't be
    resolved or the road-level lookup failed.

    percent = how far the customer is from that point, as a percentage of the
    allowed tolerance: 0% = exactly on it, 100% = right at the edge of the
    tolerance circle, >100% = outside it (fail).
    """
    if (
        customer.get("geocode_status") != "ok"
        or customer.get("geo_lat") is None
        or customer.get("lat") is None
        or customer.get("lon") is None
    ):
        return {
            "applicable": False, "distance_m": None, "percent": None,
            "tolerance_m": tolerance_m, "passed": None,
            "geocode_status": "missing_coordinates" if customer.get("lat") is None or customer.get("lon") is None else customer.get("geocode_status"),
            "basis": None, "road_name": None,
        }

    if customer.get("geo_road_status") == "ok" and customer.get("geo_road_lat") is not None:
        basis = "road"
        road_name = customer.get("geo_road_name")
        d = haversine_m(customer["lat"], customer["lon"], customer["geo_road_lat"], customer["geo_road_lon"])
    else:
        basis = "address"
        road_name = customer.get("geo_road_name")  # may still be known even if the road lookup itself failed
        d = haversine_m(customer["lat"], customer["lon"], customer["geo_lat"], customer["geo_lon"])

    if d is None:
        # Incomplete geocode record (e.g. a lookup interrupted mid-run) -
        # show "no data" rather than crash on the division below.
        return {
            "applicable": False, "distance_m": None, "percent": None,
            "tolerance_m": tolerance_m, "passed": None,
            "geocode_status": customer.get("geocode_status"),
            "basis": basis, "road_name": road_name,
        }

    percent = round((d / tolerance_m) * 100, 1) if tolerance_m > 0 else None
    return {
        "applicable": True,
        "distance_m": round(d, 1),
        "percent": percent,
        "tolerance_m": tolerance_m,
        "passed": d <= tolerance_m,
        "geocode_status": "ok",
        "basis": basis,
        "road_name": road_name,
    }
