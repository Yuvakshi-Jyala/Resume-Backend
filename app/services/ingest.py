"""Orchestrates POST /api/ingest: read new application emails, score, persist."""
from app.clients import cogitx
from app.db import processed_emails
from app.services.applicants import persist_card
from app.services.stats import recalc_stats


async def run_ingest() -> dict:
    """Auto-ingest: read new application emails, score them, and save. Meant to be
    called on a schedule (e.g. a Render Cron Job every ~15 min). Safe no-op until
    COGITX_INGEST_EXPORT_ID is set — returns ingested: 0 and touches nothing.
    """
    # Emails we've already ingested — skip them so the same applications aren't
    # re-scored (and duplicated) on every fetch.
    seen = {d["_id"] async for d in processed_emails.find({}, {"_id": 1})}

    try:
        result = await cogitx.run_ingest(seen)
    except Exception as e:
        print(f"[ingest] run_ingest failed: {e}")
        return {"ok": False, "ingested": 0, "error": str(e)}

    cards = result.get("cards", [])
    for card in cards:
        await persist_card(card)

    # Mark the emails we scored this run as processed so we don't touch them again.
    for eid in result.get("processed_ids", []):
        await processed_emails.update_one(
            {"_id": eid}, {"$set": {"_id": eid}}, upsert=True
        )

    if cards:
        await recalc_stats()
    return {"ok": True, "ingested": len(cards), "files": result.get("ingested_files", 0)}
