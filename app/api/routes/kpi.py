from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.domain.roles import abbrev_roles, display_role
from app.services.stats import compute_stats, get_stats, recalc_stats

router = APIRouter(prefix="/api")


def _parse_day(value: str | None, param: str) -> datetime | None:
    """Parse a YYYY-MM-DD query param into an aware UTC datetime (midnight)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{param} must be a date in YYYY-MM-DD format.",
        )


@router.get("/kpi")
async def kpi(refresh: bool = False, date_from: str | None = None,
              date_to: str | None = None):
    # date_from / date_to (YYYY-MM-DD) narrow the dashboard to candidates who
    # applied in that window; both ends are inclusive. Filtered requests always
    # aggregate from the live applicants collection — the cached summary only
    # covers the unfiltered view.
    start = _parse_day(date_from, "date_from")
    end = _parse_day(date_to, "date_to")
    if start and end and end < start:
        raise HTTPException(status_code=400, detail="date_to must not be before date_from.")
    # Make date_to inclusive of the whole day.
    if end:
        end = end + timedelta(days=1)

    # Normal loads read the cached summary. refresh=1 (the Refresh button) forces
    # a rebuild from the live applicants collection, so manual DB edits show up.
    if start or end:
        stats = await compute_stats(start, end)
    elif refresh:
        stats = await recalc_stats()
    else:
        stats = await get_stats()
        if not stats:
            stats = await get_stats()
    return {
        "roles": abbrev_roles(stats.get("roles", [])),
        "interviews": abbrev_roles(stats.get("interviews", [])),
        "fast_track": abbrev_roles(stats.get("fast_track", [])),
        "candidates_by_role": {
            display_role(role): cands
            for role, cands in stats.get("candidates_by_role", {}).items()
        },
    }
