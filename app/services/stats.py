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

from app.db import applicants, dashboard_stats, STATS_DOC_ID
from app.domain.bands import BANDS, band_for_applicant
from app.domain.roles import ROLE_CAPS, normalize_role, role_sort_key

# Anyone at these stages has cleared the shortlist (Option B: cumulative).
_PAST_APPLIED = {"Shortlisted", "Call Scheduled", "Call Completed"}


def applied_at(d: dict) -> datetime:
    """When this candidate applied, as an aware UTC datetime.

    New applicants carry created_at (set on insert). Rows written before that
    field existed fall back to the ObjectId's generation time, which is the
    insert timestamp — so no backfill migration is needed.
    """
    ts = d.get("created_at")
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            ts = None
    if not isinstance(ts, datetime):
        ts = d["_id"].generation_time
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def compute_stats(date_from: datetime | None = None,
                        date_to: datetime | None = None) -> dict:
    """Read all applicants and build the stats payload (does not write).

    date_from / date_to bound the window on the application date (inclusive
    lower bound, exclusive upper bound). Both default to None = no filtering.
    """
    docs = [d async for d in applicants.find({})]

    if date_from or date_to:
        docs = [
            d for d in docs
            if (date_from is None or applied_at(d) >= date_from)
            and (date_to is None or applied_at(d) < date_to)
        ]

    # Per-role aggregation.
    roles: dict[str, dict] = {}
    candidates_by_role: dict[str, list] = {}
    interviews: list[dict] = []
    fast_track: list[dict] = []

    for d in docs:
        role = normalize_role(d.get("role", ""))
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
            "bands": {b: 0 for b in BANDS},
        })
        r["applications_received"] += 1
        band = band_for_applicant(d)
        r["bands"][band] = r["bands"].get(band, 0) + 1
        if status in _PAST_APPLIED:            # Option B: cumulative shortlist
            r["shortlisted"] += 1
        if status == "Call Scheduled":
            r["calls_scheduled"] += 1
        if status == "Call Completed":
            r["calls_completed"] += 1

        if d.get("is_new"):
            r["new_count"] += 1

        if band == "Fast Track":
            fast_track.append({
                "id": str(d["_id"]),
                "date": d.get("interview_date") or "",
                "time": d.get("interview_time") or "",
                "name": d.get("name", ""),
                "role": role,
                # extras (harmless; Interview interface ignores them)
                "score": d.get("score"),
                "band": band,
                "status": status,
            })

        candidates_by_role.setdefault(role, []).append({
            "id": str(d["_id"]),
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

    roles_list = sorted(roles.values(), key=lambda r: role_sort_key(r["role"]))
    interviews.sort(key=lambda i: (i["date"], i["time"]))
    fast_track.sort(key=lambda c: (c["score"] or 0), reverse=True)

    return {
        "roles": roles_list,
        "interviews": interviews,
        "fast_track": fast_track,
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
