"""run_screening(): the resume screening workflow."""
import base64

from app.clients.cogitx.legacy import _cards_from_report, _merge_cards
from app.clients.cogitx.parsing import _card_from_json, _extract_content, _try_parse_json, _try_parse_json_list
from app.clients.cogitx.transport import _trigger
from app.core.logging import cogitx_logger as logger


async def run_screening(files: list[tuple[str, bytes, str]]) -> dict:
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

    data = await _trigger({
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
