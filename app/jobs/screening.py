"""Background worker for POST /api/screen: screens uploaded resumes and
records per-file progress in the job store so the frontend can poll."""
import asyncio

from app.clients import cogitx
from app.core.config import SCREEN_CONCURRENCY
from app.core.logging import screen_logger as logger
from app.jobs import store as jobs
from app.services.applicants import persist_card
from app.services.stats import recalc_stats


async def run_screening_job(job_id: str, blobs: list[tuple[str, bytes, str]]):
    """Screen each uploaded file independently (bounded by SCREEN_CONCURRENCY)
    so per-file status/results can be polled as they land."""
    sem = asyncio.Semaphore(SCREEN_CONCURRENCY)

    async def one(index: int, blob: tuple[str, bytes, str]):
        filename = blob[0]
        async with sem:
            await jobs.set_file(job_id, index, status="processing")
            try:
                result = await cogitx.run_screening([blob])
                cards = result.get("cards", [])
                for card in cards:
                    await persist_card(card)
                await jobs.set_file(
                    job_id, index,
                    status="done", cards=cards, report=result.get("report", ""),
                )
            except Exception as e:
                logger.exception("[screen] job %s failed for %r", job_id, filename)
                await jobs.set_file(job_id, index, status="failed", error=str(e))

    try:
        await asyncio.gather(*(one(i, b) for i, b in enumerate(blobs)))
        await recalc_stats()
    finally:
        # A job must never be left stuck in "processing".
        await jobs.finalize(job_id)
