from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import MONGODB_DB, MONGODB_URI

_client = AsyncIOMotorClient(MONGODB_URI)
db = _client[MONGODB_DB]

users = db.users
applicants = db["applicants"]
dashboard_stats = db["dashboard_stats"]
processed_emails = db["processed_emails"]
screening_jobs = db["screening_jobs"]

# The stats collection holds exactly one document addressed by this fixed id.
STATS_DOC_ID = "current"
