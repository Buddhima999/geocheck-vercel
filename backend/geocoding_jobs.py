"""One address per request, with database leases and durable row progress."""
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import analysis, geocoding
from .database import uploads_col, customers_col, geocoding_locks_col

LEASE_SECONDS = 120


def utcnow():
    return datetime.now(timezone.utc)


def process_batch(upload_id, upload_oid):
    """Return processed count. A busy lease means retry later, not failure.

    Rows remain pending until their result is saved. If an instance stops,
    another request can retry after the lease expires. Saved rows are skipped.
    """
    owner = uuid.uuid4().hex
    now = utcnow()
    lease_end = now + timedelta(seconds=LEASE_SECONDS)
    upload = uploads_col.find_one_and_update(
        {"_id": upload_oid, "$or": [
            {"geocode_lease_until": {"$exists": False}},
            {"geocode_lease_until": {"$lte": now}},
        ]},
        {"$set": {"geocode_lease_owner": owner, "geocode_lease_until": lease_end}},
        return_document=ReturnDocument.AFTER,
    )
    if upload is None:
        return 0
    provider_lock = False
    customer = None
    try:
        if geocoding.active_provider() == "nominatim":
            # Share one Nominatim lane across uploads and server instances.
            # A duplicate _id means another request owns the lane/cooldown.
            try:
                geocoding_locks_col.find_one_and_update(
                    {"_id": "nominatim", "available_at": {"$lte": now}},
                    {"$set": {"owner": owner, "available_at": lease_end}},
                    upsert=True,
                )
                provider_lock = True
            except DuplicateKeyError:
                return 0

        customer = customers_col.find_one_and_update(
            {"upload_id": upload_id, "geocode_status": "pending"},
            {"$set": {"geocode_attempt_owner": owner}},
            sort=[("_id", 1)], return_document=ReturnDocument.AFTER,
        )
        if customer is None:
            return 0
        # Preserve the existing address -> road lookup and distance rules.
        location, road = geocoding.geocode_address_full(customer["address"], timeout=10.0)
        update = {
            "geocode_status": location.status,
            "geo_lat": location.lat, "geo_lon": location.lon,
            "geo_display_name": location.display_name,
            "geocode_provider": location.provider,
            "geo_distance_m": None, "geo_road_name": location.road,
            "geo_road_status": "not_run", "geo_road_lat": None,
            "geo_road_lon": None, "geo_road_distance_m": None,
        }
        if location.status == "ok":
            update["geo_distance_m"] = analysis.haversine_m(
                customer["lat"], customer["lon"], location.lat, location.lon,
            )
            if road is not None:
                update["geo_road_status"] = road.status
                if road.status == "ok":
                    update["geo_road_lat"], update["geo_road_lon"] = road.lat, road.lon
                    update["geo_road_distance_m"] = analysis.haversine_m(
                        customer["lat"], customer["lon"], road.lat, road.lon,
                    )
        # Never publish a result after losing the lease; another instance may
        # already be retrying this customer. Never recreate a deleted row.
        if utcnow() >= lease_end:
            return 0
        result = customers_col.update_one(
            {"_id": customer["_id"], "geocode_status": "pending",
             "geocode_attempt_owner": owner},
            {"$set": update, "$unset": {"geocode_attempt_owner": ""}},
        )
        return result.modified_count
    finally:
        if provider_lock:
            geocoding_locks_col.update_one(
                {"_id": "nominatim", "owner": owner},
                {"$set": {"available_at": utcnow() + timedelta(seconds=1.1)},
                 "$unset": {"owner": ""}},
            )
        uploads_col.update_one(
            {"_id": upload_oid, "geocode_lease_owner": owner},
            {"$unset": {"geocode_lease_owner": "", "geocode_lease_until": ""}},
        )
