from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app.core.config import MAX_RESUMES_PER_REQUEST
from app.jobs import store as jobs
from app.jobs.screening import run_screening_job

router = APIRouter(prefix="/api")


@router.post("/screen", status_code=202)
async def screen(background: BackgroundTasks, files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")
    if len(files) > MAX_RESUMES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_RESUMES_PER_REQUEST} resumes are allowed per request.",
        )

    # Read the uploads now — UploadFile is closed once this request returns,
    # so the background task needs the raw bytes, not the file handles.
    blobs = [(f.filename, await f.read(), f.content_type) for f in files]

    job_id = await jobs.create_job([name for name, _, _ in blobs])
    background.add_task(run_screening_job, job_id, blobs)

    return {
        "job_id": job_id,
        "status": "processing",
        "total": len(blobs),
        "files": [{"filename": name, "status": "queued"} for name, _, _ in blobs],
    }


@router.get("/screen/{job_id}")
async def screen_status(job_id: str):
    job = await jobs.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    files = job.get("files", [])
    completed = [f for f in files if f.get("status") in ("done", "failed")]

    return {
        "job_id": job_id,
        "status": job["status"],
        "total": job["total"],
        "completed": len(completed),
        "files": files,
        # Aggregated across finished files so a caller can consume this the
        # same way the old synchronous endpoint's {report, cards} was used.
        "cards": [c for f in files for c in (f.get("cards") or [])],
        "report": "\n\n".join(f["report"] for f in files if f.get("report")),
    }
