"""Address -> coordinates, via Google (paid) or OpenStreetMap Nominatim (free).

Google's Geocoding API is a metered, billing-required API - see README.md for
what that costs. It is **not free**: Google requires a credit card on the
account before it issues a key at all, and bills per request past whatever
free monthly allowance currently applies.

So this app picks the provider automatically:
  - If GOOGLE_GEOCODING_SERVER_KEY is set in .env, it uses Google (faster,
    generally better at messy/incomplete addresses, costs money).
  - If it's not set, it automatically falls back to OpenStreetMap's Nominatim
    service - $0, no signup, no API key at all. The only cost is speed: its
    usage policy caps lookups at 1/second, so a big CSV's addresses trickle in
    over a few minutes rather than resolving instantly.

Either way, results are cached on the Customer row so re-viewing a page never
re-runs a lookup - only a fresh CSV upload triggers new ones.

Address matching is a two-step, "top down" lookup rather than a single
rooftop-precision guess:
  1. Geocode the full address as given -> a "location" point, and read off
     which road the geocoder attributed it to (most rooftop-level geocoders,
     free or paid, key their result off OpenStreetMap/Google's own address
     parsing, which always resolves to a specific road).
  2. Separately geocode just "<that road>, <that locality>" -> a "road"
     point. This is what the pass/fail check and the map circle are actually
     based on: is the customer within tolerance of their address's road,
     rather than requiring pinpoint rooftop accuracy (which free geocoders in
     particular are often a house or two off on). See geocode_address_full().
"""
import time

import httpx

from . import config

GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "GeoCheck-ISP-Tool/1.0 (contact: set GOOGLE_GEOCODING_SERVER_KEY to switch to Google)"


class GeocodeResult:
    def __init__(self, status, lat=None, lon=None, display_name=None, provider=None, road=None, locality=None):
        self.status = status  # "ok" | "not_found" | "error"
        self.lat = lat
        self.lon = lon
        self.display_name = display_name
        self.provider = provider  # "google" | "nominatim"
        self.road = road          # road/street name the geocoder attributed this address to, if any
        self.locality = locality  # city/town/village/suburb, used to disambiguate the road lookup


def active_provider():
    return "google" if config.GOOGLE_GEOCODING_SERVER_KEY else "nominatim"


def geocode_address(address, country_hint="Sri Lanka", timeout=10.0):
    """Dispatches to whichever provider is configured (see module docstring)."""
    if config.GOOGLE_GEOCODING_SERVER_KEY:
        return _geocode_google(address, country_hint, timeout)
    return _geocode_nominatim(address, country_hint, timeout)


def geocode_address_full(address, country_hint="Sri Lanka", timeout=10.0):
    """The top-down lookup: full-address location, then a separate road-level
    lookup derived from it. Returns (location: GeocodeResult, road: GeocodeResult | None).

    `road` is None when the location lookup failed or didn't yield a road
    name to search for (callers should fall back to the location point in
    that case).
    """
    location = geocode_address(address, country_hint, timeout)
    if location.status != "ok" or not location.road:
        return location, None

    # This is a second, separate request to the same provider - respect
    # Nominatim's 1/second policy between the two.
    if active_provider() == "nominatim":
        time.sleep(1.1)

    road_query = ", ".join(p for p in (location.road, location.locality) if p)
    road = geocode_address(road_query, country_hint, timeout)
    return location, road


def _geocode_google(address, country_hint, timeout):
    query = address if country_hint.lower() in address.lower() else f"{address}, {country_hint}"
    params = {"address": query, "key": config.GOOGLE_GEOCODING_SERVER_KEY, "region": "lk"}
    try:
        resp = httpx.get(GOOGLE_GEOCODE_URL, params=params, timeout=timeout)
        data = resp.json()
    except Exception as e:
        return GeocodeResult("error", display_name=str(e), provider="google")

    status = data.get("status")
    if status == "OK" and data.get("results"):
        top = data["results"][0]
        loc = top["geometry"]["location"]
        road, locality = None, None
        for comp in top.get("address_components", []):
            types = comp.get("types", [])
            if "route" in types:
                road = comp.get("long_name")
            elif locality is None and ("locality" in types or "sublocality" in types or "administrative_area_level_3" in types):
                locality = comp.get("long_name")
        return GeocodeResult(
            "ok", lat=loc["lat"], lon=loc["lng"], display_name=top.get("formatted_address"),
            provider="google", road=road, locality=locality,
        )
    elif status == "ZERO_RESULTS":
        return GeocodeResult("not_found", display_name="Google found no match for this address", provider="google")
    else:
        return GeocodeResult("error", display_name=data.get("error_message") or status, provider="google")


def _geocode_nominatim(address, country_hint, timeout):
    query = address if country_hint.lower() in address.lower() else f"{address}, {country_hint}"
    params = {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "lk", "addressdetails": 1}
    headers = {"User-Agent": NOMINATIM_USER_AGENT}
    try:
        resp = httpx.get(NOMINATIM_URL, params=params, headers=headers, timeout=timeout)
        data = resp.json()
    except Exception as e:
        return GeocodeResult("error", display_name=str(e), provider="nominatim")

    if isinstance(data, list) and data:
        top = data[0]
        addr = top.get("address", {}) or {}
        road = addr.get("road") or addr.get("pedestrian") or addr.get("footway")
        locality = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb") or addr.get("county")
        return GeocodeResult(
            "ok", lat=float(top["lat"]), lon=float(top["lon"]),
            display_name=top.get("display_name"), provider="nominatim",
            road=road, locality=locality,
        )
    return GeocodeResult("not_found", display_name="OpenStreetMap found no match for this address", provider="nominatim")
