"""
billing/google_auth.py
Google OAuth 2.0 authentication for Synthcast.

Flow:
1. User clicks "Continue with Google" on synthcast.live/login
2. Frontend redirects to GET /auth/google
3. Google redirects back to GET /auth/google/callback with a code
4. We exchange the code for user info
5. We create or find the account and return a JWT token
6. Frontend stores token and redirects to /app
"""

import os
import secrets
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from dotenv import load_dotenv

load_dotenv()

from billing.auth import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://synthcast-production.up.railway.app/auth/google/callback"
)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Simple in-memory state store to prevent CSRF
_states = set()


@router.get("/google")
async def google_login():
    """Redirect user to Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth not configured.")

    state = secrets.token_urlsafe(16)
    _states.add(state)

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
    }

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}")


@router.get("/google/callback")
async def google_callback(code: str = None, state: str = None, error: str = None):
    """Handle Google OAuth callback."""

    if error:
        return RedirectResponse("https://synthcast.live/login?error=google_denied")

    if not code or not state:
        return RedirectResponse("https://synthcast.live/login?error=invalid_callback")

    if state not in _states:
        return RedirectResponse("https://synthcast.live/login?error=invalid_state")

    _states.discard(state)

    async with httpx.AsyncClient() as client:
        # Exchange code for token
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        })

        if not token_resp.is_success:
            return RedirectResponse("https://synthcast.live/login?error=token_exchange_failed")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Get user info from Google
        user_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )

        if not user_resp.is_success:
            return RedirectResponse("https://synthcast.live/login?error=userinfo_failed")

        google_user = user_resp.json()

    email = google_user.get("email")
    name = google_user.get("name", email.split("@")[0])
    google_id = google_user.get("id")

    if not email:
        return RedirectResponse("https://synthcast.live/login?error=no_email")

    # Find or create account
    try:
        from billing.auth_routes import _get_user_by_email, _create_user, _users
        user = _get_user_by_email(email)

        if not user:
            # Create new account via Google
            import uuid
            creator_id = email.split("@")[0].lower().replace(".", "_") + "_" + str(uuid.uuid4())[:6]
            user = {
                "creator_id": creator_id,
                "email": email.lower(),
                "name": name,
                "password_hash": f"google:{google_id}",  # No password — Google auth only
                "tier": "free",
                "created_at": __import__("time").time(),
                "google_id": google_id,
                "elevenlabs_api_key": None,
                "elevenlabs_voice_id": None,
                "openai_api_key": None,
                "heygen_api_key": None,
                "heygen_avatar_id": None,
            }
            _users[email.lower()] = user

            # Also create billing account
            try:
                from billing.routes import account_store
                account_store.create(creator_id, email, name)
            except Exception as e:
                print(f"[Google Auth] Billing account creation failed: {e}")

    except Exception as e:
        print(f"[Google Auth] Account creation error: {e}")
        return RedirectResponse("https://synthcast.live/login?error=account_creation_failed")

    # Generate JWT token
    jwt_token = create_access_token({
        "sub": user["creator_id"],
        "email": email,
        "name": name,
        "tier": user.get("tier", "free"),
    })

    # Redirect to app with token in URL fragment
    # Frontend JS picks it up and stores in localStorage
    redirect_url = f"https://synthcast.live/app?token={jwt_token}&name={name}&tier={user.get('tier','free')}&creator_id={user['creator_id']}"
    return RedirectResponse(redirect_url)
