"""
billing/auth_routes.py
Authentication endpoints for Synthcast creators.

Endpoints:
  POST /auth/signup    — create account
  POST /auth/login     — get JWT token
  GET  /auth/me        — get current creator info
  POST /auth/logout    — invalidate token (client-side)
  GET  /auth/check     — check if email exists
"""

import os
import uuid
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from billing.auth import (
    SignupRequest, LoginRequest, TokenResponse, AuthenticatedCreator,
    hash_password, verify_password, create_access_token, get_current_creator
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── IN-MEMORY USER STORE (replace with DB later) ──────────────────────────────
# In production this reads/writes to PostgreSQL via the Creator model
_users = {}  # email -> {password_hash, creator_id, name, tier, created_at}


def _get_user_by_email(email: str) -> Optional[dict]:
    return _users.get(email.lower())


def _get_user_by_id(creator_id: str) -> Optional[dict]:
    for user in _users.values():
        if user["creator_id"] == creator_id:
            return user
    return None


def _create_user(email: str, name: str, password: str, tier: str = "free") -> dict:
    creator_id = email.split("@")[0].lower().replace(".", "_") + "_" + str(uuid.uuid4())[:6]
    user = {
        "creator_id": creator_id,
        "email": email.lower(),
        "name": name,
        "password_hash": hash_password(password),
        "tier": tier,
        "created_at": time.time(),
        "elevenlabs_api_key": None,
        "elevenlabs_voice_id": None,
        "openai_api_key": None,
        "heygen_api_key": None,
        "heygen_avatar_id": None,
    }
    _users[email.lower()] = user
    return user


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@router.post("/signup", response_model=TokenResponse)
async def signup(req: SignupRequest):
    """Create a new Synthcast creator account."""
    email = req.email.lower().strip()

    # Check if already exists
    if _get_user_by_email(email):
        raise HTTPException(400, "An account with this email already exists.")

    # Validate password
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    # Create user
    user = _create_user(email, req.name, req.password)

    # Also create in billing system
    try:
        from billing.routes import account_store
        account_store.create(user["creator_id"], email, req.name)
    except Exception as e:
        print(f"[Auth] Billing account creation failed: {e}")

    # Generate token
    token = create_access_token({
        "sub": user["creator_id"],
        "email": email,
        "name": req.name,
        "tier": "free",
    })

    # Send welcome email
    try:
        from billing.email_service import send_welcome_email
        send_welcome_email(email, req.name)
    except Exception as e:
        print(f"[Auth] Welcome email failed: {e}")

    return TokenResponse(
        access_token=token,
        creator_id=user["creator_id"],
        name=req.name,
        tier="free",
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Login with email and password."""
    email = req.email.lower().strip()
    user = _get_user_by_email(email)

    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password.")

    token = create_access_token({
        "sub": user["creator_id"],
        "email": email,
        "name": user["name"],
        "tier": user["tier"],
    })

    return TokenResponse(
        access_token=token,
        creator_id=user["creator_id"],
        name=user["name"],
        tier=user["tier"],
    )


@router.get("/me", response_model=AuthenticatedCreator)
async def get_me(creator: AuthenticatedCreator = Depends(get_current_creator)):
    """Get current logged-in creator info."""
    return creator


@router.get("/check")
async def check_email(email: str):
    """Check if email is already registered."""
    return {"exists": bool(_get_user_by_email(email.lower()))}


@router.post("/logout")
async def logout():
    """Logout (client should delete the token)."""
    return {"status": "logged_out", "message": "Delete your token on the client side."}


@router.post("/change-password")
async def change_password(
    current_password: str,
    new_password: str,
    creator: AuthenticatedCreator = Depends(get_current_creator)
):
    """Change creator password."""
    user = _get_user_by_id(creator.creator_id)
    if not user or not verify_password(current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect.")
    if len(new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    user["password_hash"] = hash_password(new_password)
    return {"status": "password_changed"}


# ── LOGIN PAGE ────────────────────────────────────────────────────────────────
@router.get("/login-page", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    """Serve the login/signup page."""
    return open("/app/frontend/login.html").read() if os.path.exists("/app/frontend/login.html") else "<h1>Login page not found</h1>"
