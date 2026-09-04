"""Applicant persistence and read-model shaping."""
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

from app.clients import cogitx
from app.db import applicants
from app.domain.bands import band_for_score, normalize_band
from app.domain.formatting import experience_label, skills_bucket
from app.services.stats import recalc_stats


async def persist_card(card: dict):
    """Upsert one scored candidate into the applicants collection.

    Dedupe on the candidate's email when available (stable across re-scores),
    falling back to case-insensitive name+role. This prevents duplicate rows
    when the scorer returns the same person's name in different casing.
    """
    name = card.get("name")
    if not name:
        return
    role = card.get("role", "")
    email = (card.get("email") or "").strip().lower()

    applicant = {
        "name": name,
        "role": role,
        # NOTE: status is NOT set here — it's seeded to "Applied" only on insert
        # (below), so a re-screen never overwrites a recruiter's decision.
        "score": card.get("fit_score"),
        # Full analyzer card so the dashboard can show the same detail view
        # (summary, matched/gaps, scores, verdict, interview questions).
        "analysis": card,
        "email_key": email or None,
    }

    # Match on a stable key: email if we have one, else name (case-insensitive) + role.
    if email:
        match = {"email_key": email}
    else:
        match = {
            "name": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
            "role": role,
        }

    # $set only the fields above so we never wipe an existing interview_date /
    # interview_time on a re-screen. $setOnInsert seeds them to None for brand
    # new candidates so the shape stays consistent.
    await applicants.update_one(
        match,
        {
            "$set": applicant,
            "$setOnInsert": {
                # Every new applicant starts as "Applied"; only a recruiter
                # decision (via /api/decision*) moves it to Shortlisted/Rejected.
                "status": "Applied",
                "interview_date": None,
                "interview_time": None,

                # Application date — what /api/kpi's from/to filter bounds.
                "created_at": datetime.now(timezone.utc),

                # New applicant notification
                "is_new": True,

                # Store the Outlook email ID only once
                "message_id": card.get("message_id"),
            },
        },
        upsert=True,
    )


async def list_applicants() -> list:
    result = []

    async for applicant in applicants.find():
        analysis = applicant.get("analysis", {})
        score = analysis.get("fit_score")

        if score is None:
            score = applicant.get("score")

        score = float(score or 0)
        years = analysis.get("experience_years")
        exp = experience_label(years, unknown="—")

        recommendation = normalize_band(analysis.get("band"))
        if not recommendation:
            recommendation = band_for_score(score)

        result.append({
            "id": str(applicant["_id"]),
            "name": applicant.get("name"),
            "role": applicant.get("role"),
            "match": round(score, 1),
            "experience": exp,
            "skills": skills_bucket(score),
            "recommendation": recommendation,
            "status": applicant.get("status"),
        })

    result.sort(key=lambda x: x["match"], reverse=True)
    return result


async def get_applicant(applicant_id: str) -> dict:
    applicant = await applicants.find_one({"_id": ObjectId(applicant_id)})

    if not applicant:
        raise HTTPException(status_code=404, detail="Applicant not found")

    analysis = applicant.get("analysis", {})

    years = analysis.get("experience_years")
    experience = experience_label(years, unknown="Fresher")

    return {
        "id": str(applicant["_id"]),
        "status": applicant.get("status"),

        "name": analysis.get("name"),
        "role": analysis.get("role"),
        "email": analysis.get("email"),
        "phone": analysis.get("phone"),

        "fit_score": analysis.get("fit_score"),
        "band": normalize_band(analysis.get("band")),

        "experience": experience,
        "experience_years": years,

        "summary": analysis.get("summary"),
        "verdict": analysis.get("verdict"),

        "matched_skills": analysis.get("matched_skills", []),
        "missing_skills": analysis.get("missing_skills", []),

        "technical_questions": analysis.get("interview_questions", {}).get("technical", []),
        "behavioral_questions": analysis.get("interview_questions", {}).get("behavioral", []),

        # New workflow extras, surfaced for the applicant detail view.
        "scores_line": analysis.get("scores_line"),
        "resume_strength_snapshot": analysis.get("resume_strength_snapshot", {}),
        "recruiter_notes": analysis.get("recruiter_notes", []),
    }


async def mark_role_seen(role: str):
    """Mark all new applicants in a role as seen."""
    await applicants.update_many(
        {"role": role, "is_new": True},
        {"$set": {"is_new": False}},
    )
    await recalc_stats()


async def record_decision(applicant_id: str, decision_value: str) -> dict:
    """Record a recruiter's accept/reject decision (by applicant id) and send the
    matching shortlist/rejection email via the CogitX email workflow.

    name / email / role are looked up from the stored applicant. The email send
    is wrapped in try/except so a failure never breaks this call — the decision
    is saved regardless, and email_sent reflects the real result.
    """
    try:
        oid = ObjectId(applicant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid applicant id")

    doc = await applicants.find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Applicant not found")

    analysis = doc.get("analysis") or {}
    name = analysis.get("name") or doc.get("name") or ""
    role = analysis.get("role") or doc.get("role") or ""
    email = analysis.get("email")

    new_status = "Shortlisted" if decision_value == "Shortlisted" else "Rejected"
    await applicants.update_one(
        {"_id": oid},
        {"$set": {"status": new_status, "decision": decision_value, "email_sent": False}},
    )

    email_sent = False
    if not email:
        print(f"[decision] no email on {name} — skipping send")
    else:
        print(f"[decision] sending {decision_value} email to {email} ...")
        try:
            email_sent = await cogitx.send_decision_email(name, email, role, decision_value)
        except Exception as e:  # never let an email failure break the decision
            print(f"[decision] email send FAILED for {name}: {e!r}")
            email_sent = False
        if email_sent:
            await applicants.update_one({"_id": oid}, {"$set": {"email_sent": True}})

    await recalc_stats()
    return {
        "ok": True,
        "id": applicant_id,
        "name": name,
        "role": role,
        "status": new_status,
        "email_sent": email_sent,
    }
