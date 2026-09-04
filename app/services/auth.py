"""Signup / login: password hashing, JWT minting, user persistence."""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.security import create_access_token, hash_password, verify_password
from app.db import users
from app.schemas.auth import LoginRequest, SignupRequest


async def signup(user: SignupRequest) -> dict:
    existing_user = await users.find_one({"email": user.email})

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered."
        )

    new_user = {
        "name": user.name,
        "email": user.email,
        "password_hash": hash_password(user.password),
        "created_at": datetime.now(timezone.utc),
    }

    result = await users.insert_one(new_user)

    access_token = create_access_token({
        "user_id": str(result.inserted_id),
        "email": user.email,
    })

    return {
        "message": "Signup successful.",
        "access_token": access_token,
        "user": {
            "id": str(result.inserted_id),
            "name": user.name,
            "email": user.email,
        },
    }


async def login(credentials: LoginRequest) -> dict:
    user = await users.find_one({"email": credentials.email})

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    if not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    access_token = create_access_token({
        "user_id": str(user["_id"]),
        "email": user["email"],
    })

    return {
        "message": "Login successful.",
        "access_token": access_token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "email": user["email"],
        },
    }
