from fastapi import APIRouter

router = APIRouter()


# Render deployment test
@router.get("/health")
async def health():
    return {"status": "ok"}
