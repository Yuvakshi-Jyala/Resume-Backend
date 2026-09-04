"""send_decision_email(): trigger the shortlist/rejection email workflow."""
import json

from app.clients.cogitx.config import EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET, EMAIL_EXPORT_ID
from app.clients.cogitx.transport import _trigger
from app.core.logging import cogitx_logger as logger


async def send_decision_email(name: str, email: str, role: str, decision: str) -> bool:
    """Trigger the CogitX email workflow to send the shortlist/rejection email.

    No-op (returns False) until COGITX_EMAIL_EXPORT_ID is configured, so this is
    safe to call before the email workflow exists. Returns True only if the
    workflow ran and reported success.
    """
    if not EMAIL_EXPORT_ID:
        logger.info("COGITX_EMAIL_EXPORT_ID not set — skipping email send for %s", name)
        return False

    payload = {"name": name, "email": email, "role": role, "decision": decision}
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
    logger.info("Sent %s email to %s (%s)", decision, name, email)
    return True
