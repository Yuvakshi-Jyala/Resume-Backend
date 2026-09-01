"""
Recalculation of dashboard stats from the applicants collection.

recalc_stats() reads every applicant, aggregates per-role counts + the upcoming
interview list, and overwrites the single dashboard_stats document. Call it:
  - once at startup (so the doc exists)
  - after every resume upload (so numbers stay fresh)

The OUTPUT SHAPE is deliberately identical to what /api/kpi returned before,
so the React frontend needs zero changes:
  {
    "roles": [ {role, applications_received, cap, shortlisted,
                calls_scheduled, calls_completed}, ... ],
    "interviews": [ {date, time, name, role}, ... ],
    "candidates_by_role": { role: [ {name, status, interview_date,
                                     interview_time, score}, ... ] }
  }
"""
from datetime import datetime, timezone

from db import applicants, dashboard_stats, ROLE_CAPS, STATS_DOC_ID

# Anyone at these stages has cleared the shortlist (Option B: cumulative).
_PAST_APPLIED = {"Shortlisted", "Call Scheduled", "Call Completed"}

# Stable display order for roles on the dashboard.
_ROLE_ORDER = [
    "AI/ML Engineer",
    "Solution Architect",
    "Product Manager",
    "Software Development Engineer",
    "Forward Deployed Engineer",
    "Internship",
]

# The 5 rubric decision bands, in display order.
_BANDS = ["Fast Track", "Strong Shortlist", "Shortlist", "Hold", "Reject"]


def _band_for(d: dict) -> str:
    """The candidate's band: use the workflow-assigned band if present, else
    derive it from the score using the rubric thresholds (score out of ~105)."""
    analysis = d.get("analysis") or {}
    band = analysis.get("band")
    if band:
        return band
    score = analysis.get("fit_score")
    if score is None:
        score = d.get("score")
    score = float(score or 0)
    if score >= 85:
        return "Fast Track"
    if score >= 75:
        return "Strong Shortlist"
    if score >= 65:
        return "Shortlist"
    if score >= 55:
        return "Hold"
    return "Reject"


def _role_sort_key(role: str) -> tuple:
    return (_ROLE_ORDER.index(role) if role in _ROLE_ORDER else len(_ROLE_ORDER), role)


async def compute_stats() -> dict:
    """Read all applicants and build the stats payload (does not write)."""
    docs = [d async for d in applicants.find({})]

    # Per-role aggregation.
    roles: dict[str, dict] = {}
    candidates_by_role: dict[str, list] = {}
    interviews: list[dict] = []

    for d in docs:
        role = d.get("role", "")
        status = d.get("status", "")

        r = roles.setdefault(role, {
            "role": role,
            "applications_received": 0,
            "cap": ROLE_CAPS.get(role, 0),
            "shortlisted": 0,
            "calls_scheduled": 0,
            "calls_completed": 0,
            "new_count": 0,
            # Count of candidates in each decision band (for the band graph).
            "bands": {b: 0 for b in _BANDS},
        })
        r["applications_received"] += 1
        band = _band_for(d)
        r["bands"][band] = r["bands"].get(band, 0) + 1
        if status in _PAST_APPLIED:            # Option B: cumulative shortlist
            r["shortlisted"] += 1
        if status == "Call Scheduled":
            r["calls_scheduled"] += 1
        if status == "Call Completed":
            r["calls_completed"] += 1

        if d.get("is_new"):
            r["new_count"] += 1    

        candidates_by_role.setdefault(role, []).append({
            "name": d.get("name", ""),
            "status": status,
            "interview_date": d.get("interview_date"),
            "interview_time": d.get("interview_time"),
            "score": d.get("score"),
            "analysis": d.get("analysis"),
            "decision": d.get("decision"),
            "email_sent": d.get("email_sent"),
            "is_new": d.get("is_new", False),
        })

        # Upcoming interviews = anyone with a scheduled call that has a date.
        if status == "Call Scheduled" and d.get("interview_date"):
            interviews.append({
                "date": d["interview_date"],
                "time": d.get("interview_time") or "",
                "name": d.get("name", ""),
                "role": role,
            })

    roles_list = sorted(roles.values(), key=lambda r: _role_sort_key(r["role"]))
    interviews.sort(key=lambda i: (i["date"], i["time"]))

    return {
        "roles": roles_list,
        "interviews": interviews,
        "candidates_by_role": candidates_by_role,
    }


async def recalc_stats() -> dict:
    """Compute stats and overwrite the single dashboard_stats document."""
    payload = await compute_stats()
    payload["_id"] = STATS_DOC_ID
    payload["computed_at"] = datetime.now(timezone.utc).isoformat()
    await dashboard_stats.replace_one({"_id": STATS_DOC_ID}, payload, upsert=True)
    return payload


async def get_stats() -> dict | None:
    """Read the precomputed stats document (what the KPI endpoint serves)."""
    return await dashboard_stats.find_one({"_id": STATS_DOC_ID})