"""Raw HTTP transport to the CogitX export/jobs REST API.

Confirmed from the export docs + a real response:
  - Auth: header-based x-client-id / x-client-secret (NO token exchange)
  - Trigger: POST /exports/rest-api/{export_id}/jobs
  - Poll:    GET  /exports/rest-api/{export_id}/jobs/{runId}
  - Response wraps in {statusCode, message, data:{...}}. Everything real is
    under data. Sync completions come back with data.isCompleted=true inline;
    async ones come back with data.accepted=true -> poll until isCompleted.
"""
import asyncio
import time

import httpx

from app.clients.cogitx.config import (
    BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    EXPORT_ID,
    POLL_INTERVAL,
    POLL_TIMEOUT,
)
from app.core.logging import cogitx_logger as logger


def _headers(client_id: str = "", client_secret: str = "") -> dict:
    cid = client_id or CLIENT_ID
    csec = client_secret or CLIENT_SECRET
    if not (cid and csec):
        raise RuntimeError(
            "COGITX client id / secret not set. "
            "Fill them in backend/.env before starting the backend."
        )
    return {
        "Content-Type": "application/json",
        "x-client-id": cid,
        "x-client-secret": csec,
    }


async def _trigger(body: dict, export_id: str = "", client_id: str = "",
                    client_secret: str = "") -> dict:
    """POST the job, handle sync-inline vs async-poll, return the `data` object.

    export_id defaults to the main screening export; pass a different one (e.g.
    the email workflow) to trigger that export. Each export can have its own
    client_id / client_secret; when omitted the main screening creds are used."""
    eid = export_id or EXPORT_ID
    if not eid:
        raise RuntimeError("COGITX export id not set.")
    headers = _headers(client_id, client_secret)
    trigger_url = f"{BASE_URL}/project/exports/rest-api/{eid}/jobs?waitSeconds=30"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0)) as client:
        logger.info("BASE_URL = %r", BASE_URL)
        logger.info("TRIGGER URL = %r", trigger_url)
        r = await client.post(trigger_url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json().get("data", {})

        # Sync: completed within the wait window.
        if data.get("isCompleted"):
            return data

        # Async: accepted for background processing -> poll runId.
        run_id = data.get("runId")
        status_url = data.get("statusUrl")
        if run_id:
            return await _poll(client, run_id, eid, headers, status_url)

        raise RuntimeError(f"Unexpected trigger response (no isCompleted/runId): {list(data)}")


async def _poll(client: httpx.AsyncClient, run_id: str, export_id: str = "",
                 headers: dict = None, status_url: str = "") -> dict:
    eid = export_id or EXPORT_ID
    headers = headers or _headers()
    # The poll path isn't 100% consistent across responses (statusUrl sometimes
    # omits the /project/ prefix the trigger needs), so try each candidate URL
    # until one answers. Order: server-provided statusUrl, then /project/, then bare.
    candidates = []
    if status_url:
        candidates.append(f"{BASE_URL}{status_url}")
    candidates.append(f"{BASE_URL}/project/exports/rest-api/{eid}/jobs/{run_id}")
    candidates.append(f"{BASE_URL}/exports/rest-api/{eid}/jobs/{run_id}")

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        for url in candidates:
            try:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
            except Exception:
                continue  # this URL form / transient hiccup — try the next
            if not r.text or not r.text.strip():
                continue  # still running, empty body
            try:
                data = r.json().get("data", {})
            except (ValueError, TypeError):
                continue  # non-JSON yet
            if data.get("isCompleted"):
                return data
            # A valid JSON status came back but not done yet — stick with this
            # URL form and wait for the next tick.
            break
    raise TimeoutError(f"Job {run_id} did not complete within {POLL_TIMEOUT}s")
