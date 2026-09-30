"""MongoDB connection.

Uses the standard PyMongo client. Point MONGO_URI (in .env) at a local
`mongod` or a free MongoDB Atlas cluster - see README.md.
"""
from pymongo import MongoClient

from . import config

client = MongoClient(config.MONGO_URI)
db = client[config.MONGO_DB_NAME]

uploads_col = db["uploads"]
fdps_col = db["fdps"]
customers_col = db["customers"]
settings_col = db["settings"]
pending_uploads_col = db["pending_uploads"]
geocoding_locks_col = db["geocoding_locks"]


def ensure_indexes():
    pending_uploads_col.create_index("expires_at", expireAfterSeconds=0)
    customers_col.create_index([("upload_id", 1), ("geocode_status", 1)])
    customers_col.create_index([("upload_id", 1), ("_id", 1)])


def ping():
    """Raises if MongoDB isn't reachable - used at startup to fail loudly
    with a clear message instead of a confusing error on the first request."""
    client.admin.command("ping")
