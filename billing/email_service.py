"""
billing/email_service.py
Email service for Synthcast using SendGrid.

Handles:
- Welcome/onboarding email when creator signs up
- Password reset email
- Waitlist confirmation email
- Upgrade confirmation email
"""

import os
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = "noreply@synthcast.live"
FROM_NAME = "Synthcast"
APP_URL = os.getenv("APP_URL", "https://synthcast.live")


def send_email(to_email: str, to_name: str, subject: str, html: str) -> bool:
    """Send an email via SendGrid."""
    if not SENDGRID_API_KEY:
        print(f"[Email] No SendGrid key — logging email:\nTo: {to_email}\nSubject: {subject}")
        return False

    try:
        resp = httpx.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": to_email, "name": to_name}]}],
                "from": {"email": FROM_EMAIL, "name": FROM_NAME},
                "reply_to": {"email": "hello@synthcast.live", "name": "Synthcast"},
                "subject": subject,
                "content": [{"type": "text/html", "value": html}]
            },
            timeout=10.0
        )
        success = resp.status_code == 202
        if not success:
            print(f"[Email] SendGrid error {resp.status_code}: {resp.text}")
        return success
    except Exception as e:
        print(f"[Email] Failed to send: {e}")
        return False


def _base_template(content: str) -> str:
    """Base email template with Synthcast branding."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{margin:0;padding:0;background:#07070A;font-family:system-ui,-apple-system,sans-serif}}
.wrap{{max-width:560px;margin:0 auto;padding:40px 20px}}
.card{{background:#0C0C12;border:1px solid rgba(91,79,212,0.25);border-radius:16px;padding:40px;border-top:3px solid #5B4FD4}}
.logo{{font-size:22px;font-weight:700;letter-spacing:.06em;color:#F2F2FA;margin-bottom:4px}}
.logo-accent{{color:#B8B0FF}}
.tagline{{font-size:11px;color:#54546E;letter-spacing:.1em;text-transform:uppercase;margin-bottom:32px}}
.divider{{height:1px;background:rgba(91,79,212,0.15);margin:28px 0}}
h1{{font-size:24px;font-weight:700;color:#F2F2FA;margin:0 0 12px}}
p{{font-size:14px;line-height:1.8;color:#8A8AA0;margin:0 0 16px}}
p strong{{color:#F2F2FA}}
.btn{{display:inline-block;padding:13px 32px;background:#5B4FD4;color:white;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;margin:8px 0}}
.btn:hover{{background:#8075F5}}
.btn-green{{background:#00C27A}}
.feature-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:20px 0}}
.feature{{background:rgba(91,79,212,0.06);border:1px solid rgba(91,79,212,0.15);border-radius:8px;padding:14px}}
.feature-icon{{font-size:20px;margin-bottom:6px}}
.feature-name{{font-size:13px;font-weight:600;color:#F2F2FA;margin-bottom:2px}}
.feature-desc{{font-size:11px;color:#54546E}}
.footer{{text-align:center;margin-top:28px;font-size:11px;color:#2E2E42;line-height:1.8}}
.footer a{{color:#5B4FD4;text-decoration:none}}
</style></head>
<body><div class="wrap">
<div class="card">
<div class="logo">SYNTH<span class="logo-accent">CAST</span></div>
<div class="tagline">You, Synthesized.</div>
{content}
</div>
<div class="footer">
  Synthcast · Virginia Beach, VA<br>
  <a href="{APP_URL}">synthcast.live</a> ·
  <a href="{APP_URL}/privacy">Privacy</a> ·
  <a href="{APP_URL}/terms">Terms</a><br>
  You received this because you signed up at synthcast.live
</div>
</div></body></html>"""


# ── EMAIL TEMPLATES ───────────────────────────────────────────────────────────

def send_welcome_email(email: str, name: str) -> bool:
    """Send welcome/onboarding email to new creator."""
    first_name = name.split()[0] if name else "there"
    html = _base_template(f"""
    <h1>Welcome, {first_name}. 👋</h1>
    <p>Your Synthcast account is ready. You are now part of the first wave of creators building AI avatars that go live for them.</p>
    <div class="divider"></div>
    <p><strong>Here's what you can do right now:</strong></p>
    <div class="feature-grid">
      <div class="feature">
        <div class="feature-icon">🎙</div>
        <div class="feature-name">Clone Your Voice</div>
        <div class="feature-desc">Record 30 seconds and your AI speaks exactly like you</div>
      </div>
      <div class="feature">
        <div class="feature-icon">🎬</div>
        <div class="feature-name">Create Your Avatar</div>
        <div class="feature-desc">Upload a photo and your face goes live</div>
      </div>
      <div class="feature">
        <div class="feature-icon">📱</div>
        <div class="feature-name">Go Live</div>
        <div class="feature-desc">TikTok, Twitch, YouTube — simultaneously</div>
      </div>
      <div class="feature">
        <div class="feature-icon">🧠</div>
        <div class="feature-name">Viewer Memory</div>
        <div class="feature-desc">Your avatar remembers every viewer</div>
      </div>
    </div>
    <div class="divider"></div>
    <p>You are on the <strong>Free plan</strong>. First 50 creators get 90 days free on the Creator plan — no credit card needed.</p>
    <a href="{APP_URL}/app" class="btn">Open Your Dashboard →</a>
    <div class="divider"></div>
    <p style="font-size:13px">Questions? Reply to this email or reach us at <a href="mailto:hello@synthcast.live" style="color:#B8B0FF">hello@synthcast.live</a></p>
    """)
    return send_email(email, name, "Welcome to Synthcast — Your avatar is ready", html)


def send_waitlist_confirmation(email: str, position: int) -> bool:
    """Send waitlist confirmation email."""
    html = _base_template(f"""
    <h1>You are on the list. 🎯</h1>
    <p>You are <strong>#{position}</strong> on the Synthcast waitlist. We are onboarding creators in waves — you will be among the first to get access.</p>
    <div class="divider"></div>
    <p><strong>What Synthcast does:</strong></p>
    <p>Build your AI avatar. Clone your voice. Go live on TikTok, Twitch, and YouTube simultaneously — even when you are not there. Your avatar reads comments, responds in real time, and speaks in your exact voice.</p>
    <div class="divider"></div>
    <p>While you wait — contribute your voice to the first Haitian Creole AI voice dataset:</p>
    <a href="{APP_URL}/creole" class="btn btn-green">Contribute Your Voice 🇭🇹</a>
    <div class="divider"></div>
    <p style="font-size:13px">Questions? <a href="mailto:hello@synthcast.live" style="color:#B8B0FF">hello@synthcast.live</a></p>
    """)
    return send_email(email, "", f"You are #{position} on the Synthcast waitlist", html)


def send_upgrade_confirmation(email: str, name: str, tier: str, trial_days: int = 0) -> bool:
    """Send upgrade confirmation email."""
    first_name = name.split()[0] if name else "there"
    trial_msg = f"<p>Your <strong>{trial_days}-day free trial</strong> starts now. No credit card needed until your trial ends.</p>" if trial_days else ""

    html = _base_template(f"""
    <h1>You are now on {tier.title()} plan. 🚀</h1>
    <p>Hi {first_name}, your Synthcast account has been upgraded to the <strong>{tier.title()} plan</strong>.</p>
    {trial_msg}
    <div class="divider"></div>
    <p>Your new features are active immediately. Head to your dashboard and go live.</p>
    <a href="{APP_URL}/app" class="btn">Go to Dashboard →</a>
    <div class="divider"></div>
    <p style="font-size:13px">Questions? <a href="mailto:hello@synthcast.live" style="color:#B8B0FF">hello@synthcast.live</a></p>
    """)
    return send_email(email, name, f"You are now on Synthcast {tier.title()} plan", html)


def send_password_reset_email(email: str, name: str, token: str) -> bool:
    """Send password reset email."""
    reset_url = f"https://synthcast-production.up.railway.app/auth/reset-password?token={token}"
    first_name = name.split()[0] if name else "there"

    html = _base_template(f"""
    <h1>Reset your password</h1>
    <p>Hi {first_name}, click the button below to set a new password for your Synthcast account. This link expires in <strong>30 minutes</strong>.</p>
    <a href="{reset_url}" class="btn">Reset Password →</a>
    <div class="divider"></div>
    <p style="font-size:12px;color:#54546E">If you did not request this, ignore this email. Your password will not change.</p>
    """)
    return send_email(email, name, "Reset your Synthcast password", html)
