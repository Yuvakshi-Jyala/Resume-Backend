"""run_ingest(): read new application emails, download resumes, score them."""
from app.clients.cogitx.config import INGEST_CLIENT_ID, INGEST_CLIENT_SECRET, INGEST_EXPORT_ID
from app.clients.cogitx.parsing import _download_pdf, _emails_from_ingest, _extract_content
from app.clients.cogitx.screening import run_screening
from app.clients.cogitx.transport import _trigger
from app.core.logging import cogitx_logger as logger

MAX_INGEST_RESUMES = 5  # the scoring workflow accepts up to 5 files per run


async def run_ingest(skip_ids=None) -> dict:
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

    data = await _trigger(
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
                pdf = await _download_pdf(url)
                if pdf:
                    blobs.append((name, pdf, ctype or "application/pdf"))
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

    result = await run_screening(batch)

    if not result.get("cards"):
        logger.warning("scoring returned no candidates — retrying once")
        result = await run_screening(batch)

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
