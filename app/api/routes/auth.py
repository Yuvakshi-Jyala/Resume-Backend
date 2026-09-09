from fastapi import APIRouter

from app.schemas.auth import LoginRequest, SignupRequest
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth")


@router.post("/signup")
async def signup(user: SignupRequest):
    return await auth_service.signup(user)


@router.post("/login")
async def login(credentials: LoginRequest):
    return await auth_service.login(credentials)
