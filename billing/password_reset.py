"""
billing/password_reset.py
Password reset flow for Synthcast creators.

Flow:
1. Creator clicks "Forgot password" on login page
2. POST /auth/forgot-password — generates reset token, sends email
3. Creator clicks link in email → GET /auth/reset-password?token=xxx
4. POST /auth/reset-password — sets new password

Uses SendGrid for email (free tier: 100 emails/day)
Falls back to console logging if SendGrid not configured.
"""

import os
import secrets
import hashlib
import time
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory token store (resets on redeploy — acceptable for password reset)
# Token -> {email, expires_at}
_reset_tokens = {}

RESET_TOKEN_EXPIRE_MINUTES = 30
APP_URL = os.getenv("APP_URL", "https://synthcast.live")
API_URL = os.getenv("API_URL", "https://synthcast-production.up.railway.app")


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


def send_reset_email(email: str, token: str, name: str = "") -> bool:
    """Send password reset email via SendGrid or log to console."""
    reset_url = f"{APP_URL}/reset-password?token={token}"

    # Try SendGrid first
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    if sendgrid_key:
        try:
            import httpx
            resp = httpx.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": email, "name": name}]}],
                    "from": {"email": "noreply@synthcast.live", "name": "Synthcast"},
                    "subject": "Reset your Synthcast password",
                    "content": [{
                        "type": "text/html",
                        "value": f"""
                        <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#07070A;color:#F2F2FA;border-radius:12px">
                          <div style="font-size:24px;font-weight:700;letter-spacing:.05em;margin-bottom:8px">SYNTHCAST</div>
                          <div style="font-size:14px;color:#54546E;margin-bottom:32px">Password Reset</div>
                          <p style="font-size:15px;line-height:1.7;margin-bottom:24px">
                            Hi {name or 'there'},<br><br>
                            Click the button below to reset your Synthcast password.
                            This link expires in 30 minutes.
                          </p>
                          <a href="{reset_url}" style="display:inline-block;padding:13px 32px;background:#5B4FD4;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px">Reset Password</a>
                          <p style="font-size:12px;color:#54546E;margin-top:24px">
                            If you didn't request this, ignore this email.<br>
                            Link expires in 30 minutes.
                          </p>
                          <div style="margin-top:32px;padding-top:16px;border-top:1px solid rgba(91,79,212,.2);font-size:11px;color:#54546E">
                            Synthcast · synthcast.live
                          </div>
                        </div>
                        """
                    }]
                }
            )
            return resp.status_code == 202
        except Exception as e:
            print(f"[Email] SendGrid failed: {e}")

    # Fallback — log to console
    print(f"""
[PASSWORD RESET EMAIL]
To: {email}
Reset URL: {reset_url}
Expires: 30 minutes
    """)
    return True


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    """Request a password reset email."""
    from billing.auth_routes import _get_user_by_email

    email = req.email.lower().strip()
    user = _get_user_by_email(email)

    # Always return success to prevent email enumeration
    if not user:
        return {"status": "sent", "message": "If that email exists, a reset link has been sent."}

    # Check if Google-only account
    if user.get("password_hash", "").startswith("google:"):
        return {
            "status": "google_account",
            "message": "This account uses Google sign-in. Please continue with Google."
        }

    # Generate reset token
    token = secrets.token_urlsafe(32)
    _reset_tokens[token] = {
        "email": email,
        "expires_at": time.time() + (RESET_TOKEN_EXPIRE_MINUTES * 60),
        "used": False,
    }

    # Send email via email service
    try:
        from billing.email_service import send_password_reset_email
        send_password_reset_email(email, user.get("name", ""), token)
    except Exception as e:
        # Fallback to old method
        send_reset_email(email, token, user.get("name", ""))

    return {"status": "sent", "message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    """Reset password using a valid token."""
    from billing.auth_routes import _get_user_by_email, _users

    token_data = _reset_tokens.get(req.token)

    if not token_data:
        raise HTTPException(400, "Invalid or expired reset link.")

    if token_data["used"]:
        raise HTTPException(400, "This reset link has already been used.")

    if time.time() > token_data["expires_at"]:
        del _reset_tokens[req.token]
        raise HTTPException(400, "This reset link has expired. Request a new one.")

    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    # Update password
    email = token_data["email"]
    user = _get_user_by_email(email)
    if not user:
        raise HTTPException(404, "Account not found.")

    from billing.auth import hash_password
    user["password_hash"] = hash_password(req.new_password)
    _users[email] = user

    # Mark token as used
    token_data["used"] = True

    return {"status": "success", "message": "Password updated. You can now sign in."}


@router.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
async def reset_password_page(token: str = ""):
    """Serve the reset password page."""
    token_data = _reset_tokens.get(token)
    valid = token_data and not token_data["used"] and time.time() < token_data["expires_at"]

    if not valid:
        return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Synthcast — Link Expired</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Epilogue:wght@400;500&display=swap" rel="stylesheet">
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#07070A;color:#F2F2FA;font-family:'Epilogue',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}.card{background:#0C0C12;border:1px solid rgba(91,79,212,.35);border-radius:16px;padding:40px;max-width:380px;width:100%}.logo{font-family:'Bebas Neue',sans-serif;font-size:24px;letter-spacing:.05em;margin-bottom:24px}.logo em{color:#B8B0FF;font-style:normal}h2{font-size:20px;margin-bottom:8px;color:#E03D3D}p{font-size:14px;color:#54546E;line-height:1.7;margin-bottom:24px}a{display:inline-block;padding:11px 28px;background:#5B4FD4;color:white;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600}</style>
</head><body>
<div class="card">
  <div class="logo">SYNTH<em>CAST</em></div>
  <h2>Link Expired</h2>
  <p>This password reset link has expired or already been used. Request a new one from the login page.</p>
  <a href="/login">Back to Login</a>
</div>
</body></html>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Synthcast — Reset Password</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Epilogue:wght@300;400;500&display=swap" rel="stylesheet">
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#07070A;color:#F2F2FA;font-family:'Epilogue',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}.card{{background:#0C0C12;border:1px solid rgba(91,79,212,.35);border-radius:16px;padding:40px;max-width:380px;width:100%;position:relative;overflow:hidden}}.card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,#5B4FD4,transparent)}}.logo{{font-family:'Bebas Neue',sans-serif;font-size:24px;letter-spacing:.05em;margin-bottom:8px;text-align:center}}.logo em{{color:#B8B0FF;font-style:normal}}.sub{{font-size:13px;color:#54546E;text-align:center;margin-bottom:28px}}label{{display:block;font-size:11px;color:#54546E;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px}}.input-wrap{{position:relative;margin-bottom:16px}}input{{width:100%;padding:11px 44px 11px 14px;background:#121219;border:1px solid #2E2E42;border-radius:8px;color:#F2F2FA;font-size:14px;font-family:'Epilogue',sans-serif;outline:none}}.eye{{position:absolute;right:12px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:#54546E;font-size:16px}}button.submit{{width:100%;padding:13px;border-radius:8px;font-family:'Bebas Neue',sans-serif;font-size:18px;letter-spacing:.08em;background:#5B4FD4;color:white;border:none;cursor:pointer;margin-top:4px}}button.submit:hover{{background:#8075F5}}.msg{{font-size:13px;text-align:center;margin-top:12px;min-height:20px}}.msg.success{{color:#00F598}}.msg.error{{color:#E03D3D}}</style>
</head><body>
<div class="card">
  <div class="logo">SYNTH<em>CAST</em></div>
  <div class="sub">Set your new password</div>
  <label>New Password</label>
  <div class="input-wrap">
    <input type="password" id="new-password" placeholder="Min 8 characters">
    <button class="eye" onclick="togglePw()" type="button">👁</button>
  </div>
  <label>Confirm Password</label>
  <div class="input-wrap">
    <input type="password" id="confirm-password" placeholder="Repeat password">
  </div>
  <button class="submit" onclick="resetPassword()">SET NEW PASSWORD</button>
  <div class="msg" id="msg"></div>
</div>
<script>
const API = 'https://synthcast-production.up.railway.app';
const TOKEN = '{token}';

function togglePw() {{
  const input = document.getElementById('new-password');
  input.type = input.type === 'password' ? 'text' : 'password';
}}

async function resetPassword() {{
  const pw = document.getElementById('new-password').value;
  const confirm = document.getElementById('confirm-password').value;
  const msg = document.getElementById('msg');

  if (!pw || pw.length < 8) {{
    msg.textContent = 'Password must be at least 8 characters.';
    msg.className = 'msg error';
    return;
  }}
  if (pw !== confirm) {{
    msg.textContent = 'Passwords do not match.';
    msg.className = 'msg error';
    return;
  }}

  try {{
    const resp = await fetch(API + '/auth/reset-password', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{token: TOKEN, new_password: pw}})
    }});
    const data = await resp.json();
    if (resp.ok) {{
      msg.textContent = 'Password updated! Redirecting to login...';
      msg.className = 'msg success';
      setTimeout(() => window.location.href = '/login', 2000);
    }} else {{
      msg.textContent = data.detail || 'Something went wrong.';
      msg.className = 'msg error';
    }}
  }} catch(e) {{
    msg.textContent = 'Connection error. Please try again.';
    msg.className = 'msg error';
  }}
}}
</script>
</body></html>"""
