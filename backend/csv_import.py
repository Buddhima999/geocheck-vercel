"""CSV parsing + column auto-detection.

Deliberately hand-rolled (stdlib csv module) rather than pulling in pandas -
keeps the dependency list small and the behaviour easy to reason about.
"""
import csv
import io
import re

FIELDS = {
    "fdp_id":   {"label": "FDP ID / name", "required": False,
                 "keywords": ["fdpid", "fdpname", "fdpcode", "fdp", "boxid", "cabinetid", "splitterid"]},
    "fdp_lat":  {"label": "FDP latitude", "required": True,
                 "keywords": ["fdplat", "fdplatitude", "latitudefdp", "fdp_lat"]},
    "fdp_lon":  {"label": "FDP longitude", "required": True,
                 "keywords": ["fdplon", "fdplong", "fdplongitude", "fdp_lon", "fdp_long"]},
    "cust_id":  {"label": "Customer ID", "required": False,
                 "keywords": ["customerid", "custid", "accountno", "accountnumber", "subscriberid", "circuitid"]},
    "cust_name":{"label": "Customer name", "required": False,
                 "keywords": ["customername", "custname", "clientname", "subscribername", "name"]},
    "address":  {"label": "Customer address", "required": False,
                 "keywords": ["address", "customeraddress", "fulladdress", "street", "siteaddress", "installationaddress"]},
    "cust_lat": {"label": "Customer latitude", "required": True,
                 "keywords": ["customerlat", "custlat", "clientlat", "subscriberlat", "latitude", "lat"]},
    "cust_lon": {"label": "Customer longitude", "required": True,
                 "keywords": ["customerlon", "customerlong", "custlon", "custlong", "clientlon", "subscriberlon", "longitude", "lon", "long", "lng"]},
    "line_distance": {"label": "Line distance (m, optional)", "required": False,
                 "keywords": ["linedistance", "linedistancem", "cabledistance", "dropdistance", "dropcabledistance", "distance", "distancem"]},
}

# Order matters: more specific keys are matched before generic ones like "lat"/"lon".
FIELD_ORDER = ["fdp_id", "fdp_lat", "fdp_lon", "cust_id", "cust_name", "address",
               "cust_lat", "cust_lon", "line_distance"]


def norm_header(h):
    return re.sub(r"[^a-z0-9]", "", h.lower())


def parse_csv_text(text):
    """Returns (headers: list[str], rows: list[dict])."""
    f = io.StringIO(text)
    reader = csv.reader(f)
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    data_rows = []
    for r in rows[1:]:
        if not any(cell.strip() for cell in r):
            continue
        row = {}
        for i, h in enumerate(headers):
            row[h] = r[i].strip() if i < len(r) else ""
        data_rows.append(row)
    return headers, data_rows


def autodetect_mapping(headers):
    normed = {h: norm_header(h) for h in headers}
    mapping = {}
    used = set()
    for field in FIELD_ORDER:
        keywords = FIELDS[field]["keywords"]
        best = None
        for h in headers:
            if h in used:
                continue
            nh = normed[h]
            if nh in keywords:
                best = h
                break
        if best is None:
            for h in headers:
                if h in used:
                    continue
                nh = normed[h]
                if any(kw in nh for kw in keywords):
                    best = h
                    break
        if best:
            mapping[field] = best
            used.add(best)
    return mapping


def to_float(value):
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def coord_issue(raw_lat, raw_lon, lat, lon):
    """Flags common CSV data-entry mistakes in a lat/lon pair. Returns one of
    "blank" | "unreadable" | "out_of_range" | "null_island" | None (fine).

    Doesn't check whether the point is actually in Sri Lanka or on water -
    that's a separate, meaningful check (geo_check.check_point) already
    surfaced as its own rejection reason. This only catches plain data-entry
    problems: an empty cell, text where a number was expected, a
    mathematically invalid coordinate, or the (0, 0) "null island" placeholder
    some systems use for "unknown".
    """
    raw_lat, raw_lon = (raw_lat or "").strip(), (raw_lon or "").strip()
    if lat is None or lon is None:
        return "blank" if not raw_lat and not raw_lon else "unreadable"
    if abs(lat) > 90 or abs(lon) > 180:
        return "out_of_range"
    if lat == 0 and lon == 0:
        return "null_island"
    return None


def numeric_issue(raw_value, parsed):
    """Flags a non-blank cell that couldn't be read as a number (for optional
    numeric fields like line distance, where a blank cell is fine and not an
    error - only a garbled one is)."""
    raw_value = (raw_value or "").strip()
    if raw_value and parsed is None:
        return "unreadable"
    if parsed is not None and parsed < 0:
        return "negative"
    return None
