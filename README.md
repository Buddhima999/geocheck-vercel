# GeoCheck

A CSV-based QC tool for fiber ISP field engineers, split into two pages:

- **FDP → Customer Analysis** — checks each customer's overhead (straight-line)
  distance and line (recorded cable) distance to its FDP, and rejects any FDP
  that sits outside Sri Lanka or on a known water body.
- **Customer Analysis** — geocodes each customer's address with Google and
  compares it to their uploaded coordinates (with a percentage-of-tolerance
  score), and rejects any customer whose own coordinates are outside Sri Lanka
  or on a known water body.

Click any customer's **View on map** link to see it plotted on Google Maps -
customer + FDP with a line between them on page 1, or the geocoded address as
the centre of a tolerance circle with the customer's point plotted inside/
outside it on page 2.

Built as a small Python (FastAPI) backend + **MongoDB** database + plain
HTML/JS frontend. Every upload, FDP, customer, suitability result and cached
geocode result is stored in MongoDB - switch between past uploads any time
from the dropdown in the top bar.

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Get a MongoDB database

Pick whichever is easiest for you - both work with zero code changes, you
just set `MONGO_URI` in `.env`:

**Option A - local MongoDB (no internet dependency once installed):**
```bash
# Ubuntu/Debian - add MongoDB's own apt repo, then:
sudo apt-get install -y mongodb-org
sudo systemctl enable --now mongod
```
(See [MongoDB's install docs](https://www.mongodb.com/docs/manual/administration/install-on-linux/)
for your exact distro - Windows/macOS both have simple installers too.)
Leave `MONGO_URI` unset in `.env` - the app defaults to
`mongodb://localhost:27017`.

**Option B - MongoDB Atlas (free, hosted, nothing to install or maintain):**
1. Create a free account at [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas/register)
   and spin up a free **M0** cluster (no card required for M0).
2. Add your IP (or `0.0.0.0/0` while testing) under Network Access, and
   create a database user under Database Access.
3. Copy the connection string it gives you (starts with `mongodb+srv://`)
   into `MONGO_URI` in `.env`.
This option is also the simplest if you later host the app on AWS (below) -
the database is already reachable from anywhere, so there's nothing extra to
run on the server itself.

## 3. Google API keys - what's free and what isn't

Two separate things use "Google Maps" here, and only one of them needs Google
at all:

- **Displaying the map** (the pins, the circle, the line) - this always
  needs the **Maps JavaScript API**, which needs your own Google Cloud
  project with billing enabled. There's no way around this if you want an
  actual embedded Google Map with clickable pins - Google requires a card on
  file before it issues a browser key at all, even though map loads carry a
  sizeable free monthly quota.
- **Turning an address into coordinates** (the Page 2 check) - this does
  **not** require Google. By default, with no key configured, GeoCheck
  automatically geocodes addresses using **OpenStreetMap's Nominatim
  service, which is completely free, requires no signup, and no card.** The
  only trade-off is speed: Nominatim's usage policy caps lookups at one per
  second, so a big CSV's addresses trickle in over a few minutes (you'll see
  a progress banner) instead of resolving instantly. If you later add a
  `GOOGLE_GEOCODING_SERVER_KEY`, the app automatically switches to Google
  instead (faster, usually better at messy/incomplete addresses, but billed).

So the cheapest way to run this app fully is: get a Maps JavaScript API key
only (for the pins), and leave `GOOGLE_GEOCODING_SERVER_KEY` blank so
addresses are geocoded for free via OpenStreetMap.

To set up the map key:
1. Go to [console.cloud.google.com](https://console.cloud.google.com/), create
   a project, and enable billing on it.
2. Enable the **Maps JavaScript API**.
3. Create a **browser key** (APIs & Services → Credentials), restricted to the
   Maps JavaScript API, and restricted by website to your domain (or
   `http://localhost:8000/*` while testing).
4. Copy `.env.example` to `.env` and paste it into `GOOGLE_MAPS_BROWSER_KEY`:
   ```bash
   cp .env.example .env
   ```

Only add a `GOOGLE_GEOCODING_SERVER_KEY` (a *second*, separate key restricted
to the Geocoding API and to your server's IP) if you specifically want
Google's geocoding instead of the free OpenStreetMap default - see the
pricing discussion in chat for what that currently costs.

The app still works with neither key - CSV analysis, thresholds, and the
suitability checks all run fine - you'll just see "No Google Maps browser
key configured" in the map dialog until the Maps key is in place. Address
geocoding works either way (free via OpenStreetMap, or via Google once you
add that key).

## 4. Run it

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/**. The app connects to MongoDB and creates its
collections automatically on first run - if it can't reach MongoDB at
startup, it fails immediately with a clear error naming the `MONGO_URI` it
tried, rather than failing confusingly on the first upload.

## Using it

1. **Upload CSV** and match your columns (it guesses for you). Required:
   FDP latitude/longitude, customer latitude/longitude. Optional: FDP ID,
   customer ID/name, address, line distance.
1a. If any rows have bad data - a blank coordinate, text where a number was
   expected, an out-of-range coordinate (lat/lon columns swapped is a common
   cause), a `(0, 0)` placeholder, or a garbled/negative line distance - a
   **Data quality** card appears above the tabs listing exactly how many rows
   and which ones (by ID where available). It only shows up when something's
   actually wrong; a clean CSV shows nothing. This is separate from the
   Sri-Lanka/water-body check below, which is about a *valid* point that
   happens to be in the wrong place, not bad data.
2. Addresses (if mapped) are geocoded automatically in small batches while
   the upload is open - a progress banner shows how far along it is. Each address
   gets located, then matched to its road (see "the address match" below),
   and the result is cached, so re-viewing the page never re-runs (or
   re-bills, if using Google) the lookup.
3. Review **FDP → Customer Analysis** and **Customer Analysis**. Click
   **View on map** on any row to see it plotted.
4. Adjust **Thresholds** any time - the overhead distance limit, line distance
   limit, and address match tolerance all update every open upload
   immediately, without re-uploading or re-geocoding.
5. Use the dropdown in the top bar to switch between past uploads - every
   upload is saved to the database so you can come back to it later.

## The checks, as implemented

| Check | Default | Where |
|---|---|---|
| Overhead distance (straight-line, customer↔FDP) over limit | 500 m | Page 1 |
| Line distance (your CSV's recorded cable length) over limit | 500 m | Page 1 |
| FDP outside Sri Lanka or on a known water body → FDP rejected | Sri Lanka boundary + major lakes/reservoirs | Page 1 |
| Customer's address doesn't match their uploaded coordinates | 500 m tolerance | Page 2 |
| Customer outside Sri Lanka or on a known water body → customer rejected | Sri Lanka boundary + major lakes/reservoirs | Page 2 |

**On the land/water check:** this uses a real Sri Lanka coastline polygon
(so it's meaningfully more accurate than a simple bounding box) plus a
curated list of the island's largest reservoirs, tanks and lagoons
(`backend/geodata/water_bodies.json`) as circles. It is **not exhaustive** - small
ponds, tanks, and rivers aren't covered, only the island's outline and its
biggest known water bodies. Add more entries to that JSON file (name, lat,
lon, radius_m) if you find gaps.

**On the address match - it's a "top down" check, not a rooftop-pinpoint one:**
rooftop-level geocoding (free or paid) is often a house or two off, so this
doesn't just measure straight-line distance to the exact geocoded point.
Instead, for each address it:
1. Geocodes the full address as given → a location, and reads off which
   **road** the geocoder attributed it to.
2. Geocodes that road (with its town/city) on its own → a point on/near that
   road.
3. Checks the customer's uploaded coordinates against **that road point**,
   not the rooftop point - pass if they're within the tolerance (500 m by
   default) of their own address's road.

The percentage shown is the customer's distance from that road point as a
percentage of your tolerance: 0% = right on the road, 100% = right at the
edge of the tolerance circle, over 100% = outside it (flagged as a mismatch).
If a road name couldn't be resolved for an address (rare, but happens with
very sparse addresses), it falls back to the plain rooftop point instead and
the badge is marked with a `*` so you can tell which rows used the fallback.
The map view for each customer always shows which point (road or fallback)
the check actually used.

## Data storage

Confirmed data lives in MongoDB, in four collections: `uploads`, `fdps`,
`customers` (which also holds cached geocode results), and `settings` (a
single document with your current thresholds). Nothing is deleted when you
upload a new CSV - old uploads stay selectable from the dropdown. To remove
an upload (its CSV record, FDPs and customers) from the database, select it
in the dropdown and hit **Delete**. Point `MONGO_URI`/`MONGO_DB_NAME` in
`.env` at whichever database you want to use.

A fifth collection, `pending_uploads`, stores CSV previews while you match
columns. Each preview contains a random token (`_id`), filename, headers,
rows, `created_at`, and `expires_at`. It survives backend restarts and can
be confirmed by another server instance. Confirm within 30 minutes;
expired tokens are rejected immediately, and MongoDB's TTL index removes
expired previews in the background. Confirmation atomically consumes the
token, so it cannot be reused. Invalid mapping JSON or missing required
mappings do not consume the preview. Confirmed upload records do not expire.

The database account must be able to create indexes: startup creates the
`pending_uploads.expires_at` TTL index and a customer upload/status index.

A sixth collection, `geocoding_locks`, coordinates OpenStreetMap requests
across uploads and server instances that share this database. The `nominatim`
document stores a temporary `owner` token and `available_at` time. Uploads
also have temporary `geocode_lease_owner` and `geocode_lease_until` fields;
a pending customer has `geocode_attempt_owner` during processing. These
prevent overlapping work and allow abandoned work to resume after 120 seconds.

## Resumable address processing

`POST /api/uploads/{upload_id}/geocode-batch` processes at most one pending
customer (up to two provider lookups: address, then road). It returns saved
progress plus a retry delay. Upload confirmation only saves data; the page
requests batches sequentially, refreshes results, and retries transient request
failures with a delay. Provider results such as `error` or `not_found` remain
visible in the table and are not automatically retried, as before.

Keep the selected upload open to continue. Closing the browser or switching
uploads stops requesting new work; an in-flight request may finish. Reopening
an upload resumes pending rows automatically. This is browser-driven processing,
not an always-running background worker. Finished rows are not geocoded again.
If a server stops after calling a provider but before saving, that unfinished
row may need another provider call after the lease expires.

Distance calculations, road matching, fallback behavior, and suitability rules
are unchanged. Deployment on Vercel still needs to be verified with a real build and hosted smoke test.

## Limits

Uploads are capped at 5,000 rows (`MAX_UPLOAD_ROWS`) and 3,000,000 bytes
(`MAX_UPLOAD_BYTES`) by default. The byte limit may be lowered, but cannot
exceed 3,000,000, leaving room for multipart overhead below Vercel's request
limit. Both browser and backend reject oversized files; split them into
smaller CSVs. Limits also apply to exceptionally wide/long records: at most
64 columns, 256 characters per header/filename, 4096 characters per cell,
and 100,000 UTF-8 JSON bytes per parsed row. Nothing is silently truncated.
CSV previews show up to five sample rows within a 400,000-byte sample budget.

Both analysis endpoints now return cursor pages: `?limit=100&after=<cursor>`
(default 100 customers, maximum 200). Follow `next_cursor` while `has_more`
is true. Response pages stay below 512,000 bytes, so a page may contain fewer
customers than requested. Page 1 splits large FDP customer lists across pages.
The frontend merges FDPs by ID and loads all pages before rendering tables
and percentages. Threshold changes detected during loading produce a refresh
message rather than totals calculated using mixed settings. External API
consumers must also follow pagination; one request no longer returns all rows.
A single unusually large legacy record returns a clear 413 error instead of
truncating it. Other JSON responses have a final 4,000,000-byte safety cap.

The browser still holds/renders the full loaded dataset; this is network
pagination, not virtual scrolling. Large uploads can take longer to display.
Geocoding runs in short requests while the upload is open, and does up to two
lookups per address (the address itself, then its road - see "the address
match" above). With the default free OpenStreetMap provider each of those is
paced at ~1 request/second (policy-required, not adjustable), so budget
at least a few seconds per address, plus network time; with a Google key configured, both are
billable Google Geocoding API calls with no artificial pacing.

## Hosting this on AWS

Same Lightsail recipe as before. Your MongoDB choice changes one step:

1. Launch an AWS Lightsail Ubuntu instance (a $5-10/month plan is plenty).
2. Open ports 80/443 in its firewall, attach a static IP.
3. **Database:** either use MongoDB Atlas (Option B above) and just point
   `MONGO_URI` at it from the server - nothing to install - or install
   `mongodb-org` on the same instance (Option A above) if you'd rather keep
   everything in one place with no external dependency.
4. `sudo apt install -y python3.12 python3.12-venv nginx`, then follow steps
   1 and 4 above on the server.
5. Update your Google API key restrictions to the server's IP/domain instead
   of localhost.
6. Run the app as a systemd service (same pattern as before) and put Nginx in
   front of it on port 80.
7. Since there's no login on this app, restrict access by IP in the Lightsail
   firewall, or add HTTP basic auth in Nginx, before putting it on a public
   address.
