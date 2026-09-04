"""Central place for environment configuration.

`load_dotenv()` runs here, at import time, before anything else in the app
reads an env var — every other module gets its config from this module's
constants rather than calling `os.getenv` directly, so the load order is
explicit instead of accidental.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # read backend/.env before anything below reads os.getenv

# ---------------------------------------------------------------------------
# App / server
# ---------------------------------------------------------------------------

# Max resumes accepted in a single /api/screen request, and how many of those
# are screened concurrently in the background.
MAX_RESUMES_PER_REQUEST = int(os.getenv("MAX_RESUMES_PER_REQUEST", "20"))
SCREEN_CONCURRENCY = int(os.getenv("SCREEN_CONCURRENCY", "4"))

_default_origins = (
    "http://localhost:5173,"
    "http://127.0.0.1:5173,"
    "http://localhost:3000,"
    "https://cogitx-compass-frontend-dsavamhtead3fqg8.canadacentral-01.azurewebsites.net"
)

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("FRONTEND_ORIGINS", _default_origins).split(",")
    if o.strip()
]

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ---------------------------------------------------------------------------
# Mongo
# ---------------------------------------------------------------------------

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "resume_screening")

# ---------------------------------------------------------------------------
# CogitX workflow client
# ---------------------------------------------------------------------------

COGITX_BASE_URL = os.getenv("COGITX_BASE_URL", "https://platform.cogitx.ai").rstrip("/")
COGITX_EXPORT_ID = os.getenv("COGITX_EXPORT_ID", "")

# Separate export for the shortlist/rejection email workflow. Left empty until
# that workflow is exported — while empty, send_decision_email() is a no-op.
# Each export has its own client id / secret; if the *_CLIENT_* vars are unset
# the main screening credentials are used as a fallback.
COGITX_EMAIL_EXPORT_ID = os.getenv("COGITX_EMAIL_EXPORT_ID", "")
COGITX_EMAIL_CLIENT_ID = os.getenv("COGITX_EMAIL_CLIENT_ID", "")
COGITX_EMAIL_CLIENT_SECRET = os.getenv("COGITX_EMAIL_CLIENT_SECRET", "")

# Separate export for the email-retrieval workflow (reads inbox, scores new
# applications). Left empty until exported — while empty, run_ingest() is a no-op.
COGITX_INGEST_EXPORT_ID = os.getenv("COGITX_INGEST_EXPORT_ID", "")
COGITX_INGEST_CLIENT_ID = os.getenv("COGITX_INGEST_CLIENT_ID", "")
COGITX_INGEST_CLIENT_SECRET = os.getenv("COGITX_INGEST_CLIENT_SECRET", "")

COGITX_CLIENT_ID = os.getenv("COGITX_CLIENT_ID", "")
COGITX_CLIENT_SECRET = os.getenv("COGITX_CLIENT_SECRET", "")

COGITX_POLL_INTERVAL = 3        # seconds between async status polls
COGITX_POLL_TIMEOUT = 300       # give up after this many seconds
