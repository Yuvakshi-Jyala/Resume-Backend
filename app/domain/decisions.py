"""Recruiter decisions — the stored audit record and its readers.

A decision is an AUDIT RECORD, not a second copy of status:

    {"value": "shortlisted", "at": <datetime UTC>, "raw": "shortlist"}

`value` is canonical, `raw` is exactly what the caller sent, and `at` is when
the recruiter decided. Keeping it separate from `status` means a later status
change ("call_scheduled") cannot erase the fact that this candidate was
shortlisted — which is what the old flat string, written identical to status,
could not express.

This module owns NO vocabulary of its own: the two decision values are two of
the statuses, imported from app.domain.statuses so they cannot drift apart.
Legacy rows stored `decision` as a flat string ("shortlist", "Shortlisted",
"reject"); read_decision() reads both shapes.
"""
from datetime import datetime, timezone

from app.domain.statuses import (
    REJECTED,
    SHORTLISTED,
    UNKNOWN,
    normalize_status,
    status_label,
)

# The statuses a recruiter can decide on directly. Derived, never re-declared.
DECISIONS = (SHORTLISTED, REJECTED)

DECISION_LABELS = {slug: status_label(slug) for slug in DECISIONS}


def normalize_decision(value) -> str:
    """STRICT: any known spelling -> "shortlisted"/"rejected".

    Delegates to normalize_status() so there is one alias table for the whole
    app, then rejects statuses that aren't decisions ("call scheduled" is a
    pipeline stage, not something a recruiter decides).
    """
    slug = normalize_status(value)  # raises on unknown/None/empty
    if slug not in DECISIONS:
        raise ValueError(
            f"{value!r} is a status, not a decision — expected one of "
            f"{', '.join(DECISIONS)}"
        )
    return slug


def decision_record(raw, at: datetime | None = None) -> dict:
    """Build the stored decision record from whatever the caller sent.

    Raises ValueError (via normalize_decision) on an unreadable value, so a bad
    decision never reaches the database.
    """
    return {
        "value": normalize_decision(raw),
        "at": at or datetime.now(timezone.utc),
        # The audit point: keep the caller's exact string, not the slug.
        "raw": raw if isinstance(raw, str) else str(raw),
    }


def read_decision(stored) -> dict | None:
    """Read a stored decision in EITHER shape. Never raises.

    Returns {value, at, raw, legacy} — `value` is UNKNOWN when the stored
    string can't be read, and `legacy` marks the old flat-string shape so the
    backfill knows the row needs migrating. Returns None when there is no
    decision at all.

    Never raising is deliberate: both the stats read path and the backfill scan
    walk every applicant, and neither can afford one bad row to abort the run.
    """
    if stored is None or stored == "":
        return None

    if isinstance(stored, dict):
        try:
            value = normalize_decision(stored.get("value"))
        except ValueError:
            value = UNKNOWN
        return {
            "value": value,
            "at": stored.get("at"),
            "raw": stored.get("raw", stored.get("value")),
            "legacy": False,
        }

    try:
        value = normalize_decision(stored)
    except ValueError:
        value = UNKNOWN
    return {"value": value, "at": None, "raw": stored, "legacy": True}


def decision_payload(stored) -> dict:
    """The decision fields every API read path emits.

    Flattened for the wire — the record shape stays in Mongo. An unreadable
    value surfaces as UNKNOWN rather than being hidden, so a broken row is
    visible on the dashboard instead of looking like "no decision yet".
    """
    rec = read_decision(stored)
    if rec is None:
        return {"decision": None, "decision_label": None, "decision_at": None}
    return {
        "decision": rec["value"],
        "decision_label": status_label(rec["value"]),
        "decision_at": rec["at"],
    }
