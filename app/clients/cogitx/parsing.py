"""Response-shape parsing/normalization for the CogitX workflow client."""
import asyncio
import json
import re

import httpx

from app.domain.bands import normalize_band
from app.domain.roles import normalize_role
from app.core.logging import cogitx_logger as logger


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
        # Accept both the original names (name/role) and the newer workflow
        # schema (candidate_name/matched_role) so either output shape works.
        "name": c.get("name") or c.get("candidate_name") or "",
        "role": normalize_role(c.get("role") or c.get("matched_role") or ""),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "fit_score": c.get("fit_score"),
        "band": normalize_band(c.get("band")),
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
        # Extra fields from the newer workflow schema — passed through so the
        # frontend can display them (backend doesn't otherwise use them).
        "resume_strength_snapshot": c.get("resume_strength_snapshot") or {},
        "recruiter_notes": c.get("recruiter_notes") or [],
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


async def _download_pdf(url: str, attempts: int = 3) -> bytes | None:
    """Download a resume PDF from its (temporary, signed) attachment URL.

    Retries a few times because the signed links can be slow and the network
    flaky — a truncated/failed download would make the resume unreadable and
    the scoring come back empty.
    """
    for i in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
                content = r.content
            if len(content) < 1000:  # too small to be a real PDF — treat as bad
                raise ValueError(f"suspiciously small file ({len(content)} bytes)")
            logger.info("downloaded attachment: %d bytes", len(content))
            return content
        except Exception as e:
            logger.warning("attachment download attempt %d/%d failed: %s",
                           i + 1, attempts, e)
            await asyncio.sleep(2)
    logger.warning("giving up on attachment after %d attempts", attempts)
    return None
