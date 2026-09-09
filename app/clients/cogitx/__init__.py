"""CogitX workflow client for the Resume Screening workflow. Public surface."""
from app.clients.cogitx.email import send_decision_email
from app.clients.cogitx.ingest import MAX_INGEST_RESUMES, run_ingest
from app.clients.cogitx.screening import run_screening
from app.domain.bands import normalize_band
from app.domain.roles import normalize_role

__all__ = [
    "run_screening",
    "run_ingest",
    "send_decision_email",
    "normalize_band",
    "normalize_role",
    "MAX_INGEST_RESUMES",
]
