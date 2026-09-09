from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import ALLOWED_ORIGINS
from app.jobs import store as jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    await jobs.ensure_indexes()
    await jobs.sweep_stale_jobs()
    yield


# CREATE THE APP FIRST
app = FastAPI(title="Resume Screening UI backend", lifespan=lifespan)

# THEN add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
