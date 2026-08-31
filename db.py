
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

# The stats collection holds exactly one document addressed by this fixed id.
STATS_DOC_ID = "current"

# Role application caps. Kept here so stats.py can attach them per role.
ROLE_CAPS = {
    "Machine Learning Engineer": 40,
    "Data Scientist": 30,
    "Backend Software Engineer": 50,
    "Product Manager": 25,
}