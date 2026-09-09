from fastapi import APIRouter

from app.services.ingest import run_ingest

router = APIRouter(prefix="/api")


@router.post("/ingest")
async def ingest():
    return await run_ingest()
