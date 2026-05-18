"""
billing/auth.py
JWT-based authentication for Synthcast creators.
Uses hashlib (built-in) instead of passlib to avoid bcrypt compatibility issues.
"""

import os
import time
import secrets
import hashlib
import hmac
from typing import Optional
from datetime import datetime, timedelta

from jose import JWTError, jwt
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

security = HTTPBearer(auto_error=False)


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    creator_handle: Optional[str] = None


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


def hash_password(password: str) -> str:
    salt = secrets.token_hex(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), iterations=260000)
    return f"pbkdf2:sha256:260000:{salt}:{key.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        parts = hashed.split(':')
        if len(parts) != 5 or parts[0] != 'pbkdf2':
            return False
        _, algo, iterations, salt, stored_key = parts
        key = hashlib.pbkdf2_hmac(algo, plain.encode('utf-8'), salt.encode('utf-8'), iterations=int(iterations))
        return hmac.compare_digest(key.hex(), stored_key)
    except Exception:
        return False


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
        raise HTTPException(status_code=401, detail="Invalid or expired token.", headers={"WWW-Authenticate": "Bearer"})


async def get_current_creator(credentials: HTTPAuthorizationCredentials = Depends(security)) -> AuthenticatedCreator:
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated.", headers={"WWW-Authenticate": "Bearer"})
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


async def get_optional_creator(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[AuthenticatedCreator]:
    if not credentials:
        return None
    try:
        return await get_current_creator(credentials)
    except HTTPException:
        return None
