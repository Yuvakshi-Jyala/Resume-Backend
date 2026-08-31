"""
CogitX workflow client for the Resume Screening workflow.

Confirmed from the export docs + a real response:
  - Auth: header-based x-client-id / x-client-secret (NO token exchange)
  - Trigger: POST /exports/rest-api/{export_id}/jobs
  - Poll:    GET  /exports/rest-api/{export_id}/jobs/{runId}
  - Response wraps in {statusCode, message, data:{...}}. Everything real is
    under data. Sync completions come back with data.isCompleted=true inline;
    async ones come back with data.accepted=true -> poll until isCompleted.
  - Final text: data.output.workflow_response.content
    (fallbacks: data.output.variables.text / .message)
  - Conditional routing keys off the input: files present -> screening branch,
    otherwise the KPI / hiring-status branch.
"""
import base64
import logging
import os
import time

import httpx

logger = logging.getLogger("cogitx")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[cogitx] %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)

BASE_URL = os.getenv("COGITX_BASE_URL", "https://platform.cogitx.ai").rstrip("/")
EXPORT_ID = os.getenv("COGITX_EXPORT_ID", "")
# Separate export for the shortlist/rejection email workflow. Left empty until
# that workflow is exported — while empty, send_decision_email() is a no-op.
# Each export has its own client id / secret; if the *_CLIENT_* vars are unset
# the main screening credentials are used as a fallback.
EMAIL_EXPORT_ID = os.getenv("COGITX_EMAIL_EXPORT_ID", "")
EMAIL_CLIENT_ID = os.getenv("COGITX_EMAIL_CLIENT_ID", "")
EMAIL_CLIENT_SECRET = os.getenv("COGITX_EMAIL_CLIENT_SECRET", "")
# Separate export for the email-retrieval workflow (reads inbox, scores new
# applications). Left empty until exported — while empty, run_ingest() is a no-op.
INGEST_EXPORT_ID = os.getenv("COGITX_INGEST_EXPORT_ID", "")
INGEST_CLIENT_ID = os.getenv("COGITX_INGEST_CLIENT_ID", "")
INGEST_CLIENT_SECRET = os.getenv("COGITX_INGEST_CLIENT_SECRET", "")
CLIENT_ID = os.getenv("COGITX_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("COGITX_CLIENT_SECRET", "")
KPI_TRIGGER_MESSAGE = os.getenv("KPI_TRIGGER_MESSAGE", "show kpi dashboard")

POLL_INTERVAL = 3        # seconds between async status polls
POLL_TIMEOUT = 300       # give up after this many seconds


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


def _trigger(body: dict, export_id: str = "", client_id: str = "",
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
    with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0)) as client:
        logger.info("BASE_URL = %r", BASE_URL)
        logger.info("TRIGGER URL = %r", trigger_url)
        r = client.post(trigger_url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json().get("data", {})

        # Sync: completed within the wait window.
        if data.get("isCompleted"):
            return data

        # Async: accepted for background processing -> poll runId.
        run_id = data.get("runId")
        status_url = data.get("statusUrl")
        if run_id:
            return _poll(client, run_id, eid, headers, status_url)

        raise RuntimeError(f"Unexpected trigger response (no isCompleted/runId): {list(data)}")


def _poll(client: httpx.Client, run_id: str, export_id: str = "",
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
        time.sleep(POLL_INTERVAL)
        for url in candidates:
            try:
                r = client.get(url, headers=headers)
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


def send_decision_email(name: str, email: str, role: str, decision: str) -> bool:
    """Trigger the CogitX email workflow to send the shortlist/rejection email.

    No-op (returns False) until COGITX_EMAIL_EXPORT_ID is configured, so this is
    safe to call before the email workflow exists. Returns True only if the
    workflow ran and reported success.
    """
    import json

    if not EMAIL_EXPORT_ID:
        logger.info("COGITX_EMAIL_EXPORT_ID not set — skipping email send for %s", name)
        return False

    payload = {"name": name, "email": email, "role": role, "decision": decision}
    # Send the fields as the JSON payload the email workflow's JSON Input reads.
    # Also mirror them at the top level and as text for robustness across setups.
    body = {"payload": payload, "text": json.dumps(payload), **payload}

    data = _trigger(
        body,
        export_id=EMAIL_EXPORT_ID,
        client_id=EMAIL_CLIENT_ID,
        client_secret=EMAIL_CLIENT_SECRET,
    )
    if not data.get("success", True):
        raise RuntimeError(f"Email workflow reported failure: {data.get('error')}")
    logger.info("Sent %s email to %s (%s)", decision, name, email)
    return True


def run_screening(files: list[tuple[str, bytes, str]]) -> dict:
    """
    Resumes present -> conditional IF branch -> full screening pipeline.

    Returns {report, cards}. Handles two response shapes:
      1. New: agent_2 outputs JSON {report_markdown, candidates:[...]} — read directly.
      2. Old: agent_2 outputs prose text — parse cards out of the report text.
    This lets the backend work before and after the workflow is switched to JSON.

    files: list of (filename, raw_bytes), base64-encoded for the JSON API.
    """
    encoded = [
    {
        "filename": name,
        "mimeType": mime_type,
        "base64": base64.b64encode(blob).decode("ascii"),
    }
    for name, blob, mime_type in files
    ]

    data = _trigger({
        "text": "Screen the attached resumes and produce the candidate report.",
        "files": encoded,
    })

    # The final output content is either a JSON object/string (new format) or
    # plain prose (old format). Grab the raw content first.
    raw = _extract_content(data)

    # --- DIAGNOSTIC LOGGING ---
    output = data.get("output", {}) or {}
    logger.info("SCREEN output keys: %s", list(output.keys()))
    logger.info("raw content type: %s", type(raw).__name__)
    if isinstance(raw, str):
        logger.info("raw content preview: %s", raw[:300])
    elif isinstance(raw, dict):
        logger.info("raw content keys: %s", list(raw.keys()))
    # --- END LOGGING ---

    # Try the new JSON format.
    parsed = _try_parse_json(raw)
    logger.info("FULL PARSED RESULT = %s", parsed)

    if isinstance(parsed, dict):
        logger.info("REPORT = %r", parsed.get("report_markdown"))
        logger.info("CANDIDATES = %r", parsed.get("candidates"))
    # The CogitX JSON Output node wraps its payload as {"result": {...}, "timestamp": ...}.
    # Unwrap to reach the actual {report_markdown, candidates}.
    if isinstance(parsed, dict) and "result" in parsed and "candidates" not in parsed:
        inner = parsed["result"]
        if isinstance(inner, str):
            inner = _try_parse_json(inner)
        if isinstance(inner, dict):
            parsed = inner
            logger.info("unwrapped result -> keys: %s", list(parsed.keys()))

    if isinstance(parsed, dict):
        logger.info("parsed JSON keys: %s", list(parsed.keys()))
        logger.info("candidates type: %s", type(parsed.get("candidates")).__name__)
        logger.info("report_markdown present: %s, len: %s",
                    "report_markdown" in parsed,
                    len(parsed.get("report_markdown") or "") if isinstance(parsed.get("report_markdown"), str) else "n/a")
    if isinstance(parsed, dict) and ("candidates" in parsed or "report_markdown" in parsed):
        # CogitX's JSON Output template can stringify nested arrays/objects,
        # so candidates may arrive as a JSON string — un-stringify if needed.
        cand = parsed.get("candidates")
        if isinstance(cand, str):
            cand = _try_parse_json_list(cand)

        cand = cand if isinstance(cand, list) else []

        # Always extract report first
        report = parsed.get("report_markdown") or ""
        if not isinstance(report, str):
            report = str(report)

        if cand:
            cards = [_card_from_json(c) for c in cand if isinstance(c, dict)]
            return {"report": report, "cards": cards}

        # No usable candidates but we have a report -> fall through to report parsing below.
        raw = report

    # Old format: raw is the prose report. Try structured side-channel first,
    # then fall back to parsing the report text.
    report = raw if isinstance(raw, str) else ""
    output = data.get("output", {}) or {}
    variables = output.get("variables", {}) or {}
    results = variables.get("results")
    if not isinstance(results, list):
        results = output.get("results")
    candidates = variables.get("candidates")
    if not isinstance(candidates, list):
        candidates = output.get("candidates")
    results = results if isinstance(results, list) else []
    candidates = candidates if isinstance(candidates, list) else []
    cards = _merge_cards(results, candidates)
    if not cards and report:
        cards = _cards_from_report(report)

    return {"report": report, "cards": cards}


def _extract_content(data: dict):
    """Return the final output content (may be str, dict, or JSON string)."""
    if not data.get("success", True):
        raise RuntimeError(f"Workflow reported failure: {data.get('error')}")
    output = data.get("output", {}) or {}
    wr = output.get("workflow_response", {})
    if isinstance(wr, dict) and wr.get("content") is not None:
        return wr["content"]
    variables = output.get("variables", {}) or {}
    for key in ("text", "message"):
        if variables.get(key) is not None:
            return variables[key]
    raise ValueError(f"Could not locate output content. output keys={list(output)}")


def _try_parse_json(raw):
    """If raw is a dict, return it. If it's a JSON string (possibly fenced),
    parse and return it. Otherwise return None."""
    import json
    import re
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    # strip ```json ... ``` fences if present
    m = re.match(r"^```(?:json)?\s*(.+?)\s*```$", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def _try_parse_json_list(raw):
    """Parse a JSON string that should contain a list; return [] on failure."""
    import json
    import re
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.+?)\s*```$", s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    try:
        val = json.loads(s)
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _card_from_json(c: dict) -> dict:
    """Map a candidate object from agent_2's JSON output to a frontend card."""
    q = c.get("interview_questions") or {}
    return {
        "name": c.get("name", ""),
        "role": c.get("role", ""),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "fit_score": c.get("fit_score"),
        "band": c.get("band"),
        "matched_skills": c.get("matched_skills") or [],
        "missing_skills": c.get("missing_skills") or [],
        "summary": c.get("summary") or "",
        "experience_years": c.get("experience_years"),
        "scores_line": c.get("scores_line") or "",
        "verdict": c.get("verdict") or "",
        "interview_questions": {
            "technical": q.get("technical") or [],
            "behavioral": q.get("behavioral") or [],
        },
    }


def _emails_from_ingest(raw) -> list:
    """Pull the emails list out of the retrieval workflow's JSON Output.

    Shape: {"result": {"emails": "<stringified JSON array>"}, "timestamp": ...}.
    The emails value is itself a JSON string, so it needs a second parse.
    """
    parsed = _try_parse_json(raw)
    if not isinstance(parsed, dict):
        return []
    # Unwrap the CogitX {"result": {...}} envelope.
    inner = parsed.get("result", parsed)
    if isinstance(inner, str):
        inner = _try_parse_json(inner) or {}
    emails = inner.get("emails") if isinstance(inner, dict) else None
    if isinstance(emails, str):
        emails = _try_parse_json_list(emails)
    return emails if isinstance(emails, list) else []


def _download_pdf(url: str, attempts: int = 3) -> bytes | None:
    """Download a resume PDF from its (temporary, signed) attachment URL.

    Retries a few times because the signed links can be slow and the network
    flaky — a truncated/failed download would make the resume unreadable and
    the scoring come back empty.
    """
    for i in range(attempts):
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                r = client.get(url)
                r.raise_for_status()
                content = r.content
            if len(content) < 1000:  # too small to be a real PDF — treat as bad
                raise ValueError(f"suspiciously small file ({len(content)} bytes)")
            logger.info("downloaded attachment: %d bytes", len(content))
            return content
        except Exception as e:
            logger.warning("attachment download attempt %d/%d failed: %s",
                           i + 1, attempts, e)
            time.sleep(2)
    logger.warning("giving up on attachment after %d attempts", attempts)
    return None


MAX_INGEST_RESUMES = 5  # the scoring workflow accepts up to 5 files per run


def run_ingest(skip_ids=None) -> dict:
    """Read new (unread) application emails via the retrieval workflow, download
    each resume PDF, score them through the existing screening workflow, and
    return {report, cards, ingested_files, processed_ids}.

    skip_ids: a set of Outlook email ids already ingested — those emails are
    skipped so the same applications aren't re-scored on every fetch. The
    returned processed_ids are the email ids scored this run, so the caller
    can record them as done.
    """
    skip_ids = skip_ids or set()
    if not INGEST_EXPORT_ID:
        logger.info("COGITX_INGEST_EXPORT_ID not set — skipping email ingest")
        return {"report": "", "cards": [], "ingested_files": 0, "processed_ids": []}

    data = _trigger(
        {"text": "Read new application emails."},
        export_id=INGEST_EXPORT_ID,
        client_id=INGEST_CLIENT_ID,
        client_secret=INGEST_CLIENT_SECRET,
    )
    emails = _emails_from_ingest(_extract_content(data))
    logger.info("ingest: %d emails read", len(emails))

    # Collect PDF resumes from NEW emails' attachments (skip already-processed).
    blobs = []
    metadata = []
    skipped = 0

    for em in emails:
        if not isinstance(em, dict):
            continue

        sender = (em.get("from") or "").strip().lower()
        message_id = em.get("id")

        # Already ingested this email in a previous run — don't re-score it.
        if message_id and message_id in skip_ids:
            skipped += 1
            continue

        for att in em.get("attachments") or []:
            if not isinstance(att, dict):
                continue

            ctype = (att.get("contentType") or "").lower()
            name = att.get("name") or "resume.pdf"
            is_pdf = "pdf" in ctype or name.lower().endswith(".pdf")
            url = att.get("url")

            if is_pdf and url:
                pdf = _download_pdf(url)
                if pdf:
                    blobs.append((name, pdf))
                    metadata.append({
                        "sender_email": sender,
                        "message_id": message_id,
                    })

    logger.info("ingest: %d new resume PDFs downloaded (%d emails skipped as already processed)",
                len(blobs), skipped)
    if not blobs:
        return {"report": "", "cards": [], "ingested_files": 0, "processed_ids": []}

    if len(blobs) > MAX_INGEST_RESUMES:
        logger.warning(
            "ingest: %d resumes found but only the first %d will be scored this run",
            len(blobs), MAX_INGEST_RESUMES,
        )

    # Reuse the existing scoring workflow to score the downloaded resumes.
    # The scoring agent occasionally returns an empty result (flaky LLM run);
    # retry once before giving up so a good batch of resumes isn't dropped.
    batch = blobs[:MAX_INGEST_RESUMES]
    batch_meta = metadata[:MAX_INGEST_RESUMES]

    result = run_screening(batch)

    if not result.get("cards"):
        logger.warning("scoring returned no candidates — retrying once")
        result = run_screening(batch)

    # Attach Outlook metadata to each screened card.
    for card, meta in zip(result.get("cards", []), batch_meta):
        card["sender_email"] = meta["sender_email"]
        card["message_id"] = meta["message_id"]

    result["ingested_files"] = len(batch)
    # Only mark emails processed if scoring actually produced candidates, so a
    # flaky empty run doesn't permanently skip a real application.
    result["processed_ids"] = (
        list({m["message_id"] for m in batch_meta if m.get("message_id")})
        if result.get("cards") else []
    )
    logger.info("ingest: scored %d candidates", len(result.get("cards", [])))
    return result


def run_kpi() -> str:
    """No files -> conditional ELSE branch -> hiring status summary text."""
    data = _trigger({"text": KPI_TRIGGER_MESSAGE})
    raw = _extract_content(data)
    return raw if isinstance(raw, str) else str(raw)


def _cards_from_report(report: str) -> list:
    """Fallback card builder: parse per-candidate cards out of the markdown
    report when the workflow didn't return structured data.

    Looks for detail blocks headed by:  **Name** — Role — Score/100
    and pulls Matched/Gaps/Verdict from the following lines. Also picks up
    non-shortlisted candidates from a '### Not shortlisted' bullet list.
    """
    import re

    cards = []
    lines = report.split("\n")

    # 1) Shortlisted candidates: **Name** — Role — NN/100 (or NN)
    header_re = re.compile(
        r"^\*\*(?P<name>[^*]+?)\*\*\s*[—–-]\s*(?P<role>.+?)\s*[—–-]\s*(?P<score>\d+(?:\.\d+)?)\s*(?:/\s*100)?\s*$"
    )
    matched_re = re.compile(r"^\*\*Matched:?\*\*\s*(.+)$", re.IGNORECASE)
    gaps_re = re.compile(r"^\*\*Gaps:?\*\*\s*(.+)$", re.IGNORECASE)
    verdict_re = re.compile(r"^\*\*Verdict:?\*\*\s*(.+)$", re.IGNORECASE)

    def split_skills(s: str) -> list:
        # strip trailing markdown spaces, split on commas
        return [x.strip() for x in s.replace("  ", " ").split(",") if x.strip()]

    def band_from_score(score: float) -> str:
        if score >= 85:
            return "Fast-track"
        if score >= 70:
            return "Shortlisted"
        if score >= 50:
            return "Hold"
        return "Reject"

    seen = set()
    for i, line in enumerate(lines):
        m = header_re.match(line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        role = m.group("role").strip()
        score = float(m.group("score"))

        matched, gaps, summary = [], [], ""
        # scan the next ~15 lines for this candidate's fields
        for j in range(i + 1, min(i + 16, len(lines))):
            t = lines[j].strip()
            if header_re.match(t):
                break
            mm = matched_re.match(t)
            if mm:
                matched = split_skills(mm.group(1))
            gm = gaps_re.match(t)
            if gm:
                g = gm.group(1).strip()
                gaps = [] if g.lower() in ("none", "none.") else split_skills(g)
            vm = verdict_re.match(t)
            if vm:
                summary = vm.group(1).strip()

        cards.append({
            "name": name,
            "role": role,
            "email": None,
            "phone": None,
            "fit_score": int(score) if score.is_integer() else score,
            "band": band_from_score(score),
            "matched_skills": matched,
            "missing_skills": gaps,
            "summary": summary,
            "experience_years": None,
        })

    return cards


def _merge_cards(results: list, candidates: list) -> list:
    """Join scoring (results) with profile (candidates) by candidate_name into
    a flat per-candidate card the frontend can render directly. Scoring drives
    the list; profile fields are looked up and attached where the name matches."""
    by_name = {}
    for c in candidates:
        if isinstance(c, dict) and c.get("candidate_name"):
            by_name[c["candidate_name"]] = c

    cards = []
    for r in results:
        if not isinstance(r, dict):
            continue
        name = r.get("candidate_name", "")
        prof = by_name.get(name, {})
        cards.append({
            "name": name,
            "role": r.get("matched_role") or prof.get("role_applied") or "",
            "email": prof.get("email"),
            "phone": prof.get("phone"),
            "fit_score": r.get("fit_score"),
            "band": r.get("band"),
            "matched_skills": r.get("matched_skills") or [],
            "missing_skills": r.get("missing_skills") or [],
            "summary": r.get("summary_reason") or "",
            "experience_years": prof.get("total_experience_years"),
        })
    return cards