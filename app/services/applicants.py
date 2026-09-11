"""Applicant persistence and read-model shaping."""
import re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException

from app.clients import cogitx
from app.db import applicants
from app.domain.bands import band_for_score, normalize_band
from app.domain.decisions import (
    DECISIONS,
    decision_payload,
    decision_record,
    normalize_decision,
    read_decision,
)
from app.domain.statuses import (
    APPLIED,
    REJECTED,
    SHORTLISTED,
    UNKNOWN,
    coerce_status,
    status_payload,
    status_rank,
)
from app.domain.formatting import experience_label, skills_bucket
from app.domain.rubric import effective_fit_score, rubric_extremes, rubric_total_weight
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
                # Every new applicant starts as "applied"; only a recruiter
                # decision (via /api/decision) moves it on. Canonical slug —
                # seeding a legacy spelling here would re-introduce the drift
                # the backfill exists to clear.
                "status": APPLIED,
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
        score = float(effective_fit_score(analysis, applicant.get("score")) or 0)

        years = analysis.get("experience_years")
        exp = experience_label(years, unknown="—")

        recommendation = band_for_score(score)

        result.append({
            "id": str(applicant["_id"]),
            "name": applicant.get("name"),
            "role": applicant.get("role"),
            "match": round(score, 1),
            "experience": exp,
            "skills": skills_bucket(score),
            "recommendation": recommendation,
            **status_payload(coerce_status(applicant.get("status"))),
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

    rubric = analysis.get("rubric_breakdown") or {}
    fit_score = effective_fit_score(analysis, applicant.get("score"))
    strongest, weakest = rubric_extremes(rubric)

    return {
        "id": str(applicant["_id"]),
        **status_payload(coerce_status(applicant.get("status"))),
        **decision_payload(applicant.get("decision")),

        "name": analysis.get("name"),
        "role": analysis.get("role"),
        "email": analysis.get("email"),
        "phone": analysis.get("phone"),

        "fit_score": fit_score,
        "band": band_for_score(fit_score) if fit_score is not None
                else normalize_band(analysis.get("band")),

        # The weighted breakdown the score is computed from, so the detail view
        # can show how the number was arrived at (e.g. "88 / 100").
        "rubric_breakdown": rubric,
        "rubric_total_weight": rubric_total_weight(rubric),
        "strongest_category": strongest,
        "weakest_category": weakest,

        "shortlisted": analysis.get("shortlisted"),

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

    decision_value is normalized to a canonical slug ("shortlisted"/"rejected")
    — an unrecognised value is a 400, never a silent rejection. The raw string
    the caller sent is preserved in the stored decision record.

    name / email / role are looked up from the stored applicant. The email send
    is wrapped in try/except so a failure never breaks this call — the decision
    is saved regardless, and email_sent reflects the real result.
    """
    try:
        decision = normalize_decision(decision_value)
        record = decision_record(decision_value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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

    # A decision moves the candidate to the matching status. The two fields are
    # NOT duplicates: status is current pipeline state and can move on later
    # (call_scheduled), while the decision record preserves what was decided.
    new_status = decision
    await applicants.update_one(
        {"_id": oid},
        {"$set": {"status": new_status, "decision": record, "email_sent": False}},
    )

    email_sent = False
    if not email:
        print(f"[decision] no email on {name} — skipping send")
    else:
        print(f"[decision] sending {decision} email to {email} ...")
        try:
            email_sent = await cogitx.send_decision_email(name, email, role, decision)
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
        **status_payload(new_status),
        **decision_payload(record),
        "email_sent": email_sent,
    }


# Headline bucket for a row that qualifies for several, most severe first.
_SEVERITY = ("conflict", "contradiction", "status_missing",
             "decision_migrated", "status_normalized")

_BUCKETS = _SEVERITY + ("unreadable_status", "unreadable_decision")


async def normalize_applicants(apply: bool = False) -> dict:
    """Migrate every applicant onto the canonical status/decision vocabulary.

    Rows predate the canonical vocabulary in several ways, all repaired here:
      - status spelled differently ("Shortlisted", "Call Scheduled") or missing
      - decision stored as a flat string instead of an audit record
      - status contradicting the decision on file — the original incident, where
        `decision_value == "Shortlisted"` sent everything else to "Rejected"

    What it will NOT do is guess. A status that is a LATER pipeline stage than
    the decision can explain (call_scheduled on a rejected candidate) has two
    equally defensible readings, and picking one silently is how the wrong email
    gets sent twice — those are reported as conflicts for a human to resolve.
    Values neither normalizer can read are reported, never overwritten.

    Dry run by default. Idempotent: the canonical form is a fixed point of every
    transformation below, so a second run reports everything `ok` and writes
    nothing.
    """
    rows: dict[str, list] = {b: [] for b in _BUCKETS}
    plans: list[dict] = []
    scanned = ok = 0
    now = datetime.now(timezone.utc)

    async for doc in applicants.find({}):
        scanned += 1
        stored_status = doc.get("status")
        stored_decision = doc.get("decision")

        status_present = isinstance(stored_status, str) and stored_status.strip()
        status = coerce_status(stored_status)     # APPLIED when blank
        dec = read_decision(stored_decision)

        row = {
            "id": str(doc["_id"]),
            "name": doc.get("name"),
            "role": doc.get("role"),
            "status": stored_status,
            "decision": stored_decision,
            "email_sent": bool(doc.get("email_sent")),
        }

        # Unreadable values are reported and left exactly as they are.
        if status_present and status == UNKNOWN:
            rows["unreadable_status"].append({**row, "reason": "unreadable status"})
            continue
        if dec and dec["value"] == UNKNOWN:
            rows["unreadable_decision"].append({**row, "reason": "unreadable decision"})
            continue

        updates: dict = {}
        changes: list[str] = []

        # 1. Decision shape: flat string -> audit record.
        if dec and dec["legacy"]:
            updates["decision"] = {
                "value": dec["value"],
                # Never invent a decision time we don't have. A fabricated
                # timestamp is worse than a null one.
                "at": None,
                "raw": dec["raw"],
                "migrated_at": now,
            }
            changes.append("decision_migrated")

        # 2. Status value.
        target = status
        if not status_present:
            target = dec["value"] if dec else APPLIED
            changes.append("status_missing")
        elif dec and (status == APPLIED or status in DECISIONS) \
                and status != dec["value"]:
            # The original incident: a status the decision on file contradicts.
            target = dec["value"]
            changes.append("contradiction")
        elif dec and status_rank(status) > status_rank(SHORTLISTED) \
                and dec["value"] == REJECTED:
            # Later pipeline stage, but a rejection on file. Human call.
            rows["conflict"].append({
                **row,
                "reason": "status is a later stage than the decision explains",
            })
            continue

        if target != stored_status and "status_missing" not in changes \
                and "contradiction" not in changes:
            changes.append("status_normalized")

        if target != stored_status:
            updates["status"] = target

        if not updates:
            ok += 1
            continue

        headline = next(c for c in _SEVERITY if c in changes)
        entry = {**row, "changes": changes, "new_status": updates.get("status", status)}
        for change in changes:
            rows[change].append(entry)
        plans.append({
            "row": entry,
            "updates": updates,
            "headline": headline,
            "prev_status": stored_status,
            "prev_decision": stored_decision,
        })

    result = {
        "applied": apply,
        "scanned": scanned,
        "ok": ok,
        "changed": len(plans),
        "updated": 0,
        "counts": {b: len(rows[b]) for b in _BUCKETS},
        "rows": rows,
        # Surfaced at the top level too: these are the ones needing a human.
        "conflicts": rows["conflict"],
        "unreadable": rows["unreadable_status"] + rows["unreadable_decision"],
    }

    if not apply or not plans:
        return result

    updated = 0
    for plan in plans:
        # Guard on the pre-image so a concurrent decision isn't clobbered.
        # NOTE: Mongo compares embedded documents exactly AND field-order
        # sensitively, so a record-shaped decision is guarded on the dotted
        # decision.value path rather than the whole object.
        guard = {"_id": ObjectId(plan["row"]["id"]), "status": plan["prev_status"]}
        prev_decision = plan["prev_decision"]
        if isinstance(prev_decision, dict):
            guard["decision.value"] = prev_decision.get("value")
        else:
            guard["decision"] = prev_decision

        res = await applicants.update_one(guard, {"$set": {
            **plan["updates"],
            # Audit trail: what this row held before the migration.
            "status_backfilled_at": now,
            "status_backfilled_from": {
                "status": plan["prev_status"],
                "decision": plan["prev_decision"],
            },
        }})
        updated += res.modified_count

    result["updated"] = updated
    # Rows that changed underneath the scan — re-run to re-check them.
    result["skipped"] = len(plans) - updated

    await recalc_stats()

    # Only contradictions may have had the WRONG email sent: a status that was
    # merely spelled differently still got the right one. Corrective emails are
    # deliberately not sent here, so this can be followed up deliberately.
    result["needs_email_followup"] = [
        {
            "id": p["row"]["id"],
            "name": p["row"]["name"],
            "role": p["row"]["role"],
            "emailed_as": p["prev_status"],
            "should_have_been": p["updates"].get("status"),
        }
        for p in plans
        if p["headline"] == "contradiction" and p["row"]["email_sent"]
    ]
    return result
