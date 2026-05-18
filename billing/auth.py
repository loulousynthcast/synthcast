"""
billing/auth.py
JWT-based authentication for Synthcast creators.
Handles signup, login, Google OAuth, and session management.

Install:
    pip install python-jose[cryptography] passlib[bcrypt] python-multipart
"""

import os
import time
import secrets
from typing import Optional
from datetime import datetime, timedelta

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
security = HTTPBearer(auto_error=False)


# ── MODELS ────────────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    creator_handle: Optional[str] = None  # TikTok/social handle


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    creator_id: str
    name: str
    tier: str
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class AuthenticatedCreator(BaseModel):
    creator_id: str
    email: str
    name: str
    tier: str


# ── PASSWORD UTILS ────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT UTILS ─────────────────────────────────────────────────────────────────
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── AUTH DEPENDENCY ───────────────────────────────────────────────────────────
async def get_current_creator(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> AuthenticatedCreator:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(credentials.credentials)
    creator_id = payload.get("sub")
    if not creator_id:
        raise HTTPException(status_code=401, detail="Invalid token.")
    return AuthenticatedCreator(
        creator_id=creator_id,
        email=payload.get("email", ""),
        name=payload.get("name", ""),
        tier=payload.get("tier", "free"),
    )


# Optional auth — returns None if not logged in
async def get_optional_creator(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[AuthenticatedCreator]:
    if not credentials:
        return None
    try:
        return await get_current_creator(credentials)
    except HTTPException:
        return None
