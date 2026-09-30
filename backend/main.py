import json
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from . import config, csv_import, geo_check, geocoding, analysis, models
from .database import uploads_col, fdps_col, customers_col, settings_col, ping
from .schemas import SettingsIn

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="GeoCheck")

# In-memory holding area for CSVs that have been uploaded but not yet
# confirmed (i.e. the user is still on the column-mapping screen). Keyed by a
# random token. Fine for a single-process local app; cleared on restart.
PENDING_UPLOADS = {}

DEFAULT_SETTINGS = {
    "_id": "singleton",
    "overhead_threshold_m": config.DEFAULT_OVERHEAD_THRESHOLD_M,
    "line_threshold_m": config.DEFAULT_LINE_THRESHOLD_M,
    "address_tolerance_m": config.DEFAULT_ADDRESS_TOLERANCE_M,
}


def _get_settings():
    row = settings_col.find_one({"_id": "singleton"})
    if row is None:
        settings_col.insert_one(dict(DEFAULT_SETTINGS))
        row = dict(DEFAULT_SETTINGS)
    return row


@app.on_event("startup")
def on_startup():
    try:
        ping()
    except PyMongoError as e:
        raise RuntimeError(
            f"Couldn't reach MongoDB at {config.MONGO_URI} - is it running? "
            f"See README.md for local install / Atlas setup. ({e})"
        )
    _get_settings()


# ---------------------------------------------------------------- Settings

@app.get("/api/settings")
def get_settings():
    s = _get_settings()
    return {
        "overhead_threshold_m": s["overhead_threshold_m"],
        "line_threshold_m": s["line_threshold_m"],
        "address_tolerance_m": s["address_tolerance_m"],
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn):
    settings_col.update_one(
        {"_id": "singleton"},
        {"$set": {
            "overhead_threshold_m": body.overhead_threshold_m,
            "line_threshold_m": body.line_threshold_m,
            "address_tolerance_m": body.address_tolerance_m,
        }},
        upsert=True,
    )
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {
        "google_maps_browser_key": config.GOOGLE_MAPS_BROWSER_KEY,
        "geocoding_provider": geocoding.active_provider(),  # "google" | "nominatim"
    }


# ---------------------------------------------------------------- Uploads

@app.post("/api/uploads/preview")
async def upload_preview(file: UploadFile = File(...)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    headers, rows = csv_import.parse_csv_text(text)
    if not headers or not rows:
        raise HTTPException(400, "Couldn't find any data rows in that CSV.")
    if len(rows) > config.MAX_UPLOAD_ROWS:
        raise HTTPException(400, f"That CSV has {len(rows)} rows; this app currently caps uploads at {config.MAX_UPLOAD_ROWS}.")

    mapping = csv_import.autodetect_mapping(headers)
    token = uuid.uuid4().hex
    PENDING_UPLOADS[token] = {"filename": file.filename, "headers": headers, "rows": rows, "ts": time.time()}

    return {
        "upload_token": token,
        "filename": file.filename,
        "headers": headers,
        "row_count": len(rows),
        "sample_rows": rows[:5],
        "autodetected_mapping": mapping,
        "field_defs": csv_import.FIELDS,
    }


def _run_geocoding_job(upload_id: str):
    pending = list(customers_col.find({"upload_id": upload_id, "geocode_status": "pending"}))
    provider = geocoding.active_provider()
    for i, c in enumerate(pending):
        # Top-down: geocode the full address (location), then the road it
        # resolves to (what the pass/fail check is actually based on) - see
        # geocoding.py's module docstring.
        location, road = geocoding.geocode_address_full(c["address"])
        update = {
            "geocode_status": location.status,
            "geo_lat": location.lat,
            "geo_lon": location.lon,
            "geo_display_name": location.display_name,
            "geocode_provider": location.provider,
            "geo_road_name": location.road,
            "geo_road_status": "not_run",
            "geo_road_lat": None,
            "geo_road_lon": None,
            "geo_road_distance_m": None,
        }
        if location.status == "ok":
            update["geo_distance_m"] = analysis.haversine_m(c["lat"], c["lon"], location.lat, location.lon)
            if road is not None:
                update["geo_road_status"] = road.status
                if road.status == "ok":
                    update["geo_road_lat"] = road.lat
                    update["geo_road_lon"] = road.lon
                    update["geo_road_distance_m"] = analysis.haversine_m(c["lat"], c["lon"], road.lat, road.lon)
        customers_col.update_one({"_id": c["_id"]}, {"$set": update})
        # Nominatim's usage policy caps lookups at 1/second; Google has no such limit.
        # (geocode_address_full already paces its own two internal requests.)
        if provider == "nominatim" and i < len(pending) - 1:
            time.sleep(1.1)


def _scan_row_quality(rows, mapping):
    """One independent pass over the raw CSV rows (before FDP dedup or any
    other processing) that flags plain data-entry mistakes - a blank cell,
    text where a number was expected, an out-of-range coordinate, or a
    (0, 0) "null island" placeholder. Counts are per literal CSV row, which
    is what answers "how many rows are wrong and what's wrong with them".

    This is deliberately separate from the suitability check (geo_check) -
    that check is about whether a *valid* point happens to be outside Sri
    Lanka or on water, which is meaningful information in itself and already
    shown as its own rejection reason. This is about the data being unusable
    in the first place.
    """
    issue_defs = {
        "fdp_coords_blank": "FDP coordinates are blank",
        "fdp_coords_unreadable": "FDP coordinates aren't valid numbers",
        "fdp_coords_out_of_range": "FDP coordinates are out of a valid range (lat/lon columns swapped?)",
        "fdp_coords_null_island": "FDP coordinates are (0, 0)",
        "cust_coords_blank": "Customer coordinates are blank",
        "cust_coords_unreadable": "Customer coordinates aren't valid numbers",
        "cust_coords_out_of_range": "Customer coordinates are out of a valid range (lat/lon columns swapped?)",
        "cust_coords_null_island": "Customer coordinates are (0, 0)",
        "line_distance_unreadable": "Line distance isn't a valid number",
        "line_distance_negative": "Line distance is negative",
    }
    hits = {k: [] for k in issue_defs}

    for i, row in enumerate(rows):
        row_num = i + 1  # 1-based, matches the CSV row right after the header
        fdp_ext_id = (row.get(mapping.get("fdp_id"), "") or "").strip() if mapping.get("fdp_id") else ""
        cust_ext_id = (row.get(mapping.get("cust_id"), "") or "").strip() if mapping.get("cust_id") else ""
        cust_name = (row.get(mapping.get("cust_name"), "") or "").strip() if mapping.get("cust_name") else ""
        cust_label = cust_ext_id or cust_name or f"row {row_num}"

        raw_fdp_lat, raw_fdp_lon = row.get(mapping["fdp_lat"], ""), row.get(mapping["fdp_lon"], "")
        fdp_lat, fdp_lon = csv_import.to_float(raw_fdp_lat), csv_import.to_float(raw_fdp_lon)
        issue = csv_import.coord_issue(raw_fdp_lat, raw_fdp_lon, fdp_lat, fdp_lon)
        if issue:
            hits[f"fdp_coords_{issue}"].append({"row": row_num, "label": fdp_ext_id or f"row {row_num}"})

        raw_cust_lat, raw_cust_lon = row.get(mapping["cust_lat"], ""), row.get(mapping["cust_lon"], "")
        cust_lat, cust_lon = csv_import.to_float(raw_cust_lat), csv_import.to_float(raw_cust_lon)
        issue = csv_import.coord_issue(raw_cust_lat, raw_cust_lon, cust_lat, cust_lon)
        if issue:
            hits[f"cust_coords_{issue}"].append({"row": row_num, "label": cust_label})

        if mapping.get("line_distance"):
            raw_line = row.get(mapping["line_distance"], "")
            line_val = csv_import.to_float(raw_line)
            issue = csv_import.numeric_issue(raw_line, line_val)
            if issue:
                hits[f"line_distance_{issue}"].append({"row": row_num, "label": cust_label})

    issues, affected_rows = [], set()
    for key, label in issue_defs.items():
        rows_hit = hits[key]
        if rows_hit:
            issues.append({"type": key, "label": label, "count": len(rows_hit), "examples": rows_hit[:10]})
            affected_rows.update(r["row"] for r in rows_hit)

    return {
        "total_rows": len(rows),
        "flagged_rows": len(affected_rows),
        "clean_rows": len(rows) - len(affected_rows),
        "issues": issues,
    }


@app.post("/api/uploads/confirm")
def upload_confirm(
    background_tasks: BackgroundTasks,
    upload_token: str = Form(...),
    mapping_json: str = Form(...),
):
    pending = PENDING_UPLOADS.pop(upload_token, None)
    if pending is None:
        raise HTTPException(400, "That upload has expired - please upload the CSV again.")
    mapping = json.loads(mapping_json)

    for req in ("fdp_lat", "fdp_lon", "cust_lat", "cust_lon"):
        if not mapping.get(req):
            raise HTTPException(400, f"Missing required column mapping: {req}")

    rows = pending["rows"]
    quality = _scan_row_quality(rows, mapping)
    upload_doc = {
        "filename": pending["filename"],
        "uploaded_at": datetime.utcnow(),
        "row_count": len(rows),
        "has_line_distance": bool(mapping.get("line_distance")),
        "has_address": bool(mapping.get("address")),
        "data_quality": quality,
    }
    upload_id = str(uploads_col.insert_one(upload_doc).inserted_id)

    # --- Pass 1: dedupe FDPs (by ext_id, or by lat/lon if no id column) and
    # insert them all in one batch, running the suitability check for each.
    fdp_keys_order, fdp_docs, seen = [], [], set()
    for row in rows:
        fdp_ext_id = (row.get(mapping.get("fdp_id"), "") or "").strip() if mapping.get("fdp_id") else ""
        fdp_lat = csv_import.to_float(row.get(mapping["fdp_lat"]))
        fdp_lon = csv_import.to_float(row.get(mapping["fdp_lon"]))
        key = fdp_ext_id or f"{fdp_lat},{fdp_lon}"
        if key in seen:
            continue
        seen.add(key)
        fdp_keys_order.append(key)
        if fdp_lat is None or fdp_lon is None:
            is_ok, reason = False, "Missing FDP coordinates"
        else:
            is_ok, reason = geo_check.check_point(fdp_lat, fdp_lon)
        fdp_docs.append({
            "upload_id": upload_id, "ext_id": fdp_ext_id or None,
            "lat": fdp_lat, "lon": fdp_lon,
            "is_suitable": is_ok, "rejection_reason": reason,
        })

    fdp_id_map = {}
    if fdp_docs:
        result = fdps_col.insert_many(fdp_docs)
        fdp_id_map = {k: str(oid) for k, oid in zip(fdp_keys_order, result.inserted_ids)}

    # --- Pass 2: build + insert customers, resolving each one's fdp_id from
    # the map above.
    customer_docs = []
    pending_geocode_count = 0
    for row in rows:
        fdp_ext_id = (row.get(mapping.get("fdp_id"), "") or "").strip() if mapping.get("fdp_id") else ""
        fdp_lat = csv_import.to_float(row.get(mapping["fdp_lat"]))
        fdp_lon = csv_import.to_float(row.get(mapping["fdp_lon"]))
        key = fdp_ext_id or f"{fdp_lat},{fdp_lon}"
        fdp_id = fdp_id_map.get(key)

        cust_lat = csv_import.to_float(row.get(mapping["cust_lat"]))
        cust_lon = csv_import.to_float(row.get(mapping["cust_lon"]))
        address = (row.get(mapping.get("address"), "") or "").strip() if mapping.get("address") else None
        line_dist = csv_import.to_float(row.get(mapping.get("line_distance"))) if mapping.get("line_distance") else None

        if cust_lat is None or cust_lon is None:
            is_ok, reason = False, "Missing customer coordinates"
        else:
            is_ok, reason = geo_check.check_point(cust_lat, cust_lon)

        geocode_status = "not_run"
        if address:
            geocode_status = "pending"
            pending_geocode_count += 1

        customer_docs.append({
            "upload_id": upload_id, "fdp_id": fdp_id,
            "ext_id": (row.get(mapping.get("cust_id"), "") or "").strip() if mapping.get("cust_id") else None,
            "name": (row.get(mapping.get("cust_name"), "") or "").strip() if mapping.get("cust_name") else None,
            "address": address or None,
            "lat": cust_lat, "lon": cust_lon,
            "line_distance_m": line_dist,
            "is_suitable": is_ok, "rejection_reason": reason,
            "geocode_status": geocode_status,
            "geo_lat": None, "geo_lon": None, "geo_display_name": None, "geo_distance_m": None,
            "geo_road_name": None, "geo_road_status": "not_run", "geo_road_lat": None, "geo_road_lon": None,
            "geo_road_distance_m": None,
        })

    if customer_docs:
        customers_col.insert_many(customer_docs)

    if pending_geocode_count:
        # Always works - falls back to free OpenStreetMap Nominatim automatically
        # when no Google key is configured (see geocoding.py).
        background_tasks.add_task(_run_geocoding_job, upload_id)

    return {
        "upload_id": upload_id, "row_count": len(rows),
        "pending_geocode_count": pending_geocode_count,
        "geocoding_provider": geocoding.active_provider(),
        "data_quality": quality,
    }


@app.get("/api/uploads")
def list_uploads():
    uploads = list(uploads_col.find().sort("_id", -1).limit(20))
    return [
        {"id": str(u["_id"]), "filename": u["filename"], "uploaded_at": u["uploaded_at"].isoformat(), "row_count": u["row_count"]}
        for u in uploads
    ]


@app.get("/api/uploads/{upload_id}/quality")
def upload_quality(upload_id: str):
    u = uploads_col.find_one({"_id": models.oid(upload_id)})
    if u is None:
        raise HTTPException(404, "Upload not found")
    return u.get("data_quality") or {"total_rows": u.get("row_count", 0), "flagged_rows": 0, "clean_rows": u.get("row_count", 0), "issues": []}


@app.get("/api/uploads/{upload_id}/status")
def upload_status(upload_id: str):
    total = customers_col.count_documents({"upload_id": upload_id})
    with_address = customers_col.count_documents({"upload_id": upload_id, "address": {"$ne": None}})
    done = customers_col.count_documents({"upload_id": upload_id, "geocode_status": {"$in": ["ok", "not_found", "error"]}})
    return {"total_customers": total, "with_address": with_address, "geocode_done": done, "geocode_pending": with_address - done}


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str):
    """Permanently removes an upload and all of its FDP/customer rows."""
    try:
        upload_id_obj = models.oid(upload_id)
    except ValueError:
        raise HTTPException(404, "Upload not found")
    if uploads_col.find_one({"_id": upload_id_obj}) is None:
        raise HTTPException(404, "Upload not found")
    uploads_col.delete_one({"_id": upload_id_obj})
    fdps_col.delete_many({"upload_id": upload_id})
    customers_col.delete_many({"upload_id": upload_id})
    return {"ok": True}


# ---------------------------------------------------------------- Page 1

@app.get("/api/uploads/{upload_id}/page1")
def page1(upload_id: str):
    settings = _get_settings()
    fdps = list(fdps_col.find({"upload_id": upload_id}))
    if not fdps:
        raise HTTPException(404, "Upload not found (or has no FDPs)")

    ok_fdps, rejected_fdps = [], []
    for fdp in fdps:
        fdp_id = str(fdp["_id"])
        customers_out = []
        for c in customers_col.find({"fdp_id": fdp_id}):
            overhead = analysis.overhead_check_dict(c, fdp, settings["overhead_threshold_m"])
            line = analysis.line_distance_check_dict(c, settings["line_threshold_m"])
            customers_out.append({
                "id": str(c["_id"]), "ext_id": c.get("ext_id"), "name": c.get("name"),
                "lat": c.get("lat"), "lon": c.get("lon"),
                "overhead": overhead, "line": line,
            })
        entry = {
            "id": fdp_id, "ext_id": fdp.get("ext_id"), "lat": fdp.get("lat"), "lon": fdp.get("lon"),
            "is_suitable": fdp["is_suitable"], "rejection_reason": fdp.get("rejection_reason"),
            "customers": customers_out,
        }
        (ok_fdps if fdp["is_suitable"] else rejected_fdps).append(entry)

    return {
        "thresholds": {"overhead_threshold_m": settings["overhead_threshold_m"], "line_threshold_m": settings["line_threshold_m"]},
        "ok_fdps": ok_fdps, "rejected_fdps": rejected_fdps,
    }


# ---------------------------------------------------------------- Page 2

@app.get("/api/uploads/{upload_id}/page2")
def page2(upload_id: str):
    settings = _get_settings()
    customers = list(customers_col.find({"upload_id": upload_id}))
    if not customers:
        raise HTTPException(404, "Upload not found (or has no customers)")

    ok_customers, rejected_customers = [], []
    for c in customers:
        match = analysis.address_match_check_dict(c, settings["address_tolerance_m"])
        entry = {
            "id": str(c["_id"]), "ext_id": c.get("ext_id"), "name": c.get("name"), "address": c.get("address"),
            "lat": c.get("lat"), "lon": c.get("lon"),
            "is_suitable": c["is_suitable"], "rejection_reason": c.get("rejection_reason"),
            "address_match": match,
        }
        (ok_customers if c["is_suitable"] else rejected_customers).append(entry)

    return {"thresholds": {"address_tolerance_m": settings["address_tolerance_m"]},
            "ok_customers": ok_customers, "rejected_customers": rejected_customers}


# ---------------------------------------------------------------- Map detail

@app.get("/api/customers/{customer_id}/map")
def customer_map(customer_id: str):
    c = customers_col.find_one({"_id": models.oid(customer_id)})
    if c is None:
        raise HTTPException(404, "Customer not found")
    fdp = fdps_col.find_one({"_id": models.oid(c["fdp_id"])}) if c.get("fdp_id") else None
    return {
        "customer": {"id": str(c["_id"]), "ext_id": c.get("ext_id"), "name": c.get("name"), "lat": c.get("lat"), "lon": c.get("lon"), "address": c.get("address")},
        "fdp": ({"id": str(fdp["_id"]), "ext_id": fdp.get("ext_id"), "lat": fdp.get("lat"), "lon": fdp.get("lon")} if fdp else None),
        "geocode": {
            "status": c.get("geocode_status"), "lat": c.get("geo_lat"), "lon": c.get("geo_lon"),
            "display_name": c.get("geo_display_name"), "distance_m": c.get("geo_distance_m"),
            "road_status": c.get("geo_road_status"), "road_name": c.get("geo_road_name"),
            "road_lat": c.get("geo_road_lat"), "road_lon": c.get("geo_road_lon"),
            "road_distance_m": c.get("geo_road_distance_m"),
        },
    }


# ---------------------------------------------------------------- Frontend

app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
