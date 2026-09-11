import secrets

from fastapi import APIRouter, Header, HTTPException

from app.core.config import BACKFILL_TOKEN
from app.schemas.applicants import Decision
from app.services import applicants as applicants_service

router = APIRouter(prefix="/api")


@router.get("/applicants")
async def get_applicants():
    return await applicants_service.list_applicants()


@router.get("/applicants/{applicant_id}")
async def get_applicant(applicant_id: str):
    return await applicants_service.get_applicant(applicant_id)


@router.post("/roles/{role}/mark-seen")
async def mark_role_seen(role: str):
    """Mark all new applicants in a role as seen."""
    await applicants_service.mark_role_seen(role)
    return {"ok": True}


@router.post("/decision")
async def decision(body: Decision):
    """Record a recruiter's accept/reject decision (by applicant id) and send the
    matching shortlist/rejection email via the CogitX email workflow.

    The frontend only sends {id, decision}; name / email / role are looked up from
    the stored applicant.
    """
    return await applicants_service.record_decision(body.id, body.decision)


@router.post("/admin/backfill/applicant-vocabulary")
async def backfill_applicant_vocabulary(
    apply: bool = False,
    x_admin_token: str | None = Header(default=None),
):
    """Migrate every applicant onto the canonical status/decision vocabulary.

    Repairs rows that predate the vocabulary: statuses spelled differently or
    missing, decisions stored as a flat string rather than an audit record, and
    statuses contradicting the decision on file (the bug that sent shortlisted
    candidates a rejection email). Conflicts and unreadable values are reported
    for a human, never guessed at.

    Dry run by default — a plain POST reports what would change and writes
    nothing. `?apply=true` writes the corrections and recomputes dashboard
    stats, and requires the X-Admin-Token header to match BACKFILL_TOKEN. Safe
    to re-run: once a row is canonical it is no longer a candidate.
    """
    if apply:
        if not BACKFILL_TOKEN:
            raise HTTPException(
                status_code=403,
                detail="BACKFILL_TOKEN is not configured — apply=true is disabled.",
            )
        # Constant-time compare so the token can't be recovered by timing.
        if not x_admin_token or not secrets.compare_digest(
            x_admin_token, BACKFILL_TOKEN
        ):
            raise HTTPException(status_code=403, detail="Invalid admin token")

    return await applicants_service.normalize_applicants(apply=apply)


@router.post("/admin/backfill/decision-status", deprecated=True)
async def backfill_decision_status(
    apply: bool = False,
    x_admin_token: str | None = Header(default=None),
):
    """Deprecated alias for /admin/backfill/applicant-vocabulary."""
    return await backfill_applicant_vocabulary(apply=apply, x_admin_token=x_admin_token)
