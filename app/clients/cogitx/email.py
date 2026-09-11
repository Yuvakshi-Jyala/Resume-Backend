"""send_decision_email(): trigger the shortlist/rejection email workflow."""
import json

from app.clients.cogitx.config import EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET, EMAIL_EXPORT_ID
from app.clients.cogitx.transport import _trigger
from app.core.logging import cogitx_logger as logger
from app.domain.decisions import DECISIONS
from app.domain.statuses import status_label

# WIRE CONTRACT — the external CogitX email workflow branches on this exact
# string to choose which email the candidate receives. It is NOT the internal
# slug and must never become one: internally we store "shortlisted", the
# workflow receives "Shortlisted". Converting here, at the client boundary,
# means no service can leak a slug to CogitX by forgetting to convert.
_WIRE_DECISION = {slug: status_label(slug) for slug in DECISIONS}


async def send_decision_email(name: str, email: str, role: str, decision: str) -> bool:
    """Trigger the CogitX email workflow to send the shortlist/rejection email.

    `decision` is a canonical decision slug ("shortlisted"/"rejected"); it is
    translated to the workflow's expected label on the way out.

    No-op (returns False) until COGITX_EMAIL_EXPORT_ID is configured, so this is
    safe to call before the email workflow exists. Returns True only if the
    workflow ran and reported success.
    """
    if not EMAIL_EXPORT_ID:
        logger.info("COGITX_EMAIL_EXPORT_ID not set — skipping email send for %s", name)
        return False

    wire = _WIRE_DECISION.get(decision)
    if wire is None:
        # Deliberately raise rather than fall back to a default. A silent
        # fallback here is exactly what sent shortlisted candidates a rejection
        # email; failing to send beats sending the wrong one. record_decision()
        # catches this and saves the decision with email_sent=False.
        raise ValueError(f"refusing to email an unknown decision {decision!r}")

    payload = {"name": name, "email": email, "role": role, "decision": wire}
    # Send the fields as the JSON payload the email workflow's JSON Input reads.
    # Also mirror them at the top level and as text for robustness across setups.
    body = {"payload": payload, "text": json.dumps(payload), **payload}

    data = await _trigger(
        body,
        export_id=EMAIL_EXPORT_ID,
        client_id=EMAIL_CLIENT_ID,
        client_secret=EMAIL_CLIENT_SECRET,
    )
    if not data.get("success", True):
        raise RuntimeError(f"Email workflow reported failure: {data.get('error')}")
    logger.info("Sent %s email to %s (%s)", wire, name, email)
    return True
