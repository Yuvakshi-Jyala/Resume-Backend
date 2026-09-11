"""Canonical applicant-status vocabulary — the single source of truth.

Status used to be a bare string written by three call sites and by hand in
Mongo, with no agreed spelling: production carries "Shortlisted", "shortlist",
"reject", "Call Scheduled" and rows with no status at all. stats.py hardcoded
the set {"Shortlisted", "Call Scheduled", "Call Completed"} to mean "has
cleared the shortlist", so any row spelled differently was silently miscounted
on the dashboard.

Storage and the API wire format are the SLUG ("shortlisted"). The LABEL
("Shortlisted") is display-only — and is also what the external CogitX email
workflow expects, which is why the slug->label conversion lives in
app/clients/cogitx/email.py rather than anywhere a caller could forget it.

Two normalizers, on purpose:
  normalize_status()  STRICT  — writes and API input. Raises on anything it
                                doesn't recognise, so a typo surfaces as a 4xx
                                instead of being recorded as the wrong status.
  coerce_status()     LENIENT — reading rows already in the database. Never
                                raises: one hand-edited row must not take down
                                the whole KPI recompute.
"""

APPLIED = "applied"
SHORTLISTED = "shortlisted"
CALL_SCHEDULED = "call_scheduled"
CALL_COMPLETED = "call_completed"
REJECTED = "rejected"

# Read-only sentinel for stored values we can't read. NEVER written to Mongo —
# only ever produced by coerce_status(), so callers can count and report the
# rows they couldn't interpret instead of inventing a status for them.
UNKNOWN = "unknown"

# The forward pipeline, in order. REJECTED is deliberately NOT here: it is a
# terminal, off-ladder state rather than a stage, so a rejected candidate never
# counts as having cleared the shortlist — whatever they were before.
STATUS_PIPELINE = (APPLIED, SHORTLISTED, CALL_SCHEDULED, CALL_COMPLETED)

STATUSES = STATUS_PIPELINE + (REJECTED,)

STATUS_LABELS = {
    APPLIED: "Applied",
    SHORTLISTED: "Shortlisted",
    CALL_SCHEDULED: "Call Scheduled",
    CALL_COMPLETED: "Call Completed",
    REJECTED: "Rejected",
    UNKNOWN: "Unknown",
}

_RANKS = {slug: i for i, slug in enumerate(STATUS_PIPELINE)}

# Every spelling seen in production or sent by a caller, keyed by _key(). The
# decision aliases ("accept", "decline", ...) live here too so the whole app
# shares one alias table — app.domain.decisions narrows this set rather than
# keeping a second copy of it.
_CANONICAL_STATUSES = {
    "applied": APPLIED,
    "apply": APPLIED,
    "new": APPLIED,
    "application received": APPLIED,
    "received": APPLIED,
    "pending": APPLIED,

    "shortlisted": SHORTLISTED,
    "shortlist": SHORTLISTED,
    "shortlisting": SHORTLISTED,
    "accept": SHORTLISTED,
    "accepted": SHORTLISTED,
    "approve": SHORTLISTED,
    "approved": SHORTLISTED,
    "selected": SHORTLISTED,
    "yes": SHORTLISTED,

    "call scheduled": CALL_SCHEDULED,
    "callscheduled": CALL_SCHEDULED,
    "interview scheduled": CALL_SCHEDULED,
    "scheduled": CALL_SCHEDULED,

    "call completed": CALL_COMPLETED,
    "callcompleted": CALL_COMPLETED,
    "call done": CALL_COMPLETED,
    "interview completed": CALL_COMPLETED,
    "interviewed": CALL_COMPLETED,
    "completed": CALL_COMPLETED,

    "rejected": REJECTED,
    "reject": REJECTED,
    "rejection": REJECTED,
    "decline": REJECTED,
    "declined": REJECTED,
    "deny": REJECTED,
    "denied": REJECTED,
    "not selected": REJECTED,
    "no": REJECTED,
}


def _key(value: str) -> str:
    """Lowercase, hyphens/underscores to spaces, whitespace collapsed."""
    return " ".join(value.strip().lower().replace("-", " ").replace("_", " ").split())


def normalize_status(value) -> str:
    """STRICT: map any known spelling to a canonical slug.

    Raises ValueError for None, non-strings, empty strings and anything
    unrecognised. Use on every write path and on API input.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"status must be one of {', '.join(STATUSES)} (got {value!r})"
        )

    slug = _CANONICAL_STATUSES.get(_key(value))
    if slug is None:
        raise ValueError(
            f"Unknown status {value!r} — expected one of {', '.join(STATUSES)}"
        )
    return slug


def coerce_status(value, default: str = APPLIED) -> str:
    """LENIENT: the slug for an already-stored status. READ paths only.

    Missing/empty -> `default` (APPLIED: persist_card seeds a status on every
    insert, so a blank row is an applicant nobody has acted on yet).
    Unrecognised -> UNKNOWN. Never raises — a dashboard that drops one bad row
    beats a dashboard that 500s.
    """
    if not isinstance(value, str) or not value.strip():
        return default
    return _CANONICAL_STATUSES.get(_key(value), UNKNOWN)


def status_label(slug: str) -> str:
    """Display label for a slug. Unknown slugs are titled, never dropped."""
    return STATUS_LABELS.get(slug) or str(slug or "").replace("_", " ").title()


def status_rank(slug: str) -> int:
    """Position on the forward pipeline; -1 for REJECTED/UNKNOWN (off-ladder)."""
    return _RANKS.get(slug, -1)


def has_reached(slug: str, target: str) -> bool:
    """True if `slug` is at or past `target` on the pipeline.

    Replaces stats._PAST_APPLIED: has_reached(x, SHORTLISTED) is the cumulative
    "cleared the shortlist" test. REJECTED and UNKNOWN return False by
    construction (rank -1) — a rejected candidate does not count as shortlisted
    even if they once were. has_reached(x, REJECTED) is always False; ask
    is_terminal() instead, since "reached rejected" isn't a pipeline question.
    """
    return status_rank(slug) >= status_rank(target) >= 0


def is_terminal(slug: str) -> bool:
    """Whether this status ends the candidate's pipeline."""
    return slug == REJECTED


def status_payload(slug: str) -> dict:
    """The status fields every API read path emits.

    The single place the wire shape is decided — `status` carries the slug and
    `status_label` the display string. Changing the contract means changing
    this function, not every call site.
    """
    return {"status": slug, "status_label": status_label(slug)}
