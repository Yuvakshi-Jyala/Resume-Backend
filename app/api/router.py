from fastapi import APIRouter

from app.api.routes import applicants, auth, health, ingest, kpi, screening

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(kpi.router)
api_router.include_router(screening.router)
api_router.include_router(ingest.router)
api_router.include_router(applicants.router)
