
import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "resume_screening")

_client = AsyncIOMotorClient(MONGODB_URI)
db = _client[MONGODB_DB]
users = db.users
applicants = db["applicants"]
dashboard_stats = db["dashboard_stats"]
processed_emails = db["processed_emails"]
screening_jobs = db["screening_jobs"]

# The stats collection holds exactly one document addressed by this fixed id.
STATS_DOC_ID = "current"

# Role application caps. Kept here so stats.py can attach them per role.
ROLE_CAPS = {
    "AI/ML Engineer": 30,
    "Solution Architect": 30,
    "Product Manager": 30,
    "Software Development Engineer": 30,
    "Forward Deployed Engineer": 30,
    "Internship": 30,
}