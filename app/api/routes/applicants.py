from fastapi import APIRouter

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
