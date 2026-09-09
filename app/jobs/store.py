"""Background-screening job store, backed by the `screening_jobs` Mongo collection.

A job tracks one /api/screen request: one document per file uploaded together,
each progressing queued -> processing -> done|failed independently so the
frontend can poll GET /api/screen/{job_id} and render results as they land.
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.db import screening_jobs

# Jobs (and their embedded results) auto-expire this long after creation, via
# a TTL index on created_at — keeps the collection from growing unbounded.
SCREENING_JOB_TTL = 24 * 60 * 60  # seconds

# A job whose status is still "processing" after this long is assumed to be
# orphaned by a process restart (background tasks don't survive one) and is
# swept to "failed" on startup rather than polled forever.
STALE_JOB_AGE = timedelta(hours=1)


async def ensure_indexes():
    """Create the TTL index. Idempotent — safe to call on every startup."""
    await screening_jobs.create_index(
        "created_at", expireAfterSeconds=SCREENING_JOB_TTL
    )


async def sweep_stale_jobs():
    """Fail any job left "processing" by a previous process (e.g. a restart
    mid-run, since background tasks don't survive one) so pollers don't wait
    on it forever."""
    cutoff = datetime.now(timezone.utc) - STALE_JOB_AGE
    await screening_jobs.update_many(
        {"status": "processing", "created_at": {"$lt": cutoff}},
        {
            "$set": {
                "status": "failed",
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def create_job(filenames: list[str]) -> str:
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    await screening_jobs.insert_one({
        "_id": job_id,
        "status": "processing",
        "created_at": now,
        "updated_at": now,
        "total": len(filenames),
        "files": [
            {
                "filename": name,
                "status": "queued",
                "cards": [],
                "report": None,
                "error": None,
            }
            for name in filenames
        ],
    })
    return job_id


async def set_file(job_id: str, index: int, **fields):
    """Merge `fields` into files[index] of the job (status, cards, report, error)."""
    update = {f"files.{index}.{k}": v for k, v in fields.items()}
    update["updated_at"] = datetime.now(timezone.utc)
    await screening_jobs.update_one({"_id": job_id}, {"$set": update})


async def finalize(job_id: str):
    """Set the job's top-level status once every file has finished.

    "failed" only when every file failed; any at least partial success is
    "done" so the frontend can show whatever did come through.
    """
    doc = await screening_jobs.find_one({"_id": job_id}, {"files": 1})
    files = (doc or {}).get("files", [])
    all_failed = bool(files) and all(f.get("status") == "failed" for f in files)
    await screening_jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "failed" if all_failed else "done",
                "updated_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
            }
        },
    )


async def get_job(job_id: str) -> dict | None:
    return await screening_jobs.find_one({"_id": job_id})
