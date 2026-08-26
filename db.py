"""
MongoDB connection for the Resume Screening backend.

Uses motor (async driver) so it plays nicely with FastAPI's async routes.
Connection string comes from MONGODB_URI in backend/.env, e.g.:

  MONGODB_URI=mongodb+srv://yuvakshij_db_user:PASSWORD@resume-analyzer.bnx0efb.mongodb.net/?appName=Resume-Analyzer
  MONGODB_DB=resume_screening

Two collections:
  - applicants       : one document per candidate (source of truth for counts)
  - dashboard_stats  : a single precomputed document the KPI endpoint reads
"""
import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "resume_screening")

_client = AsyncIOMotorClient(MONGODB_URI)
db = _client[MONGODB_DB]

applicants = db["applicants"]
dashboard_stats = db["dashboard_stats"]
# Outlook email ids already ingested, so the same applications aren't re-scored
# on every fetch. Each doc is {"_id": <email id>}.
processed_emails = db["processed_emails"]

# The stats collection holds exactly one document, addressed by this fixed id.
STATS_DOC_ID = "current"

# Role application caps. Kept here so stats.py can attach them per role.
ROLE_CAPS = {
    "Machine Learning Engineer": 40,
    "Data Scientist": 30,
    "Backend Software Engineer": 50,
    "Product Manager": 25,
}