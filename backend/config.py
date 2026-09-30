"""Configuration loaded from environment variables / .env file."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")

# Google API keys - you must create your own (see README.md).
GOOGLE_MAPS_BROWSER_KEY = os.environ.get("GOOGLE_MAPS_BROWSER_KEY", "")
GOOGLE_GEOCODING_SERVER_KEY = os.environ.get("GOOGLE_GEOCODING_SERVER_KEY", "")

# MongoDB connection. Point this at a local `mongod` (the default) or a free
# MongoDB Atlas cluster - see README.md for both options.
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "geocheck")

# Upload limits (kept small/simple; adjust if you need bigger CSVs)
MAX_UPLOAD_ROWS = int(os.environ.get("MAX_UPLOAD_ROWS", "5000"))

# Default thresholds (metres), used the first time the app runs.
DEFAULT_OVERHEAD_THRESHOLD_M = float(os.environ.get("DEFAULT_OVERHEAD_THRESHOLD_M", "500"))
DEFAULT_LINE_THRESHOLD_M = float(os.environ.get("DEFAULT_LINE_THRESHOLD_M", "500"))
DEFAULT_ADDRESS_TOLERANCE_M = float(os.environ.get("DEFAULT_ADDRESS_TOLERANCE_M", "500"))

GEODATA_DIR = BASE_DIR / "geodata"
