"""
synthcast/api/main.py

FastAPI server for the Synthcast agent engine.
Exposes the response engine, session management,
viewer memory, and stream control as HTTP endpoints.

Run with:
    uvicorn api.main:app --reload --port 8000

Docs available at:
    http://localhost:8000/docs
"""

import os
import time
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv()

# ── Import agent modules ───────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.response_engine import (
    ResponseEngine, BrandKit, IncomingComment,
    CommentType, Priority
)
from agent.llm_clients import get_llm_client
from agent.memory import get_memory_store
from agent.queue import CommentQueue
from billing.routes import router as billing_router
from database import init_db


# ── APP STATE ─────────────────────────────────────────────────────────────────

class AppState:
    """Singleton holding live session state."""
    engine: Optional[ResponseEngine] = None
    queue: Optional[CommentQueue] = None
    session_start: Optional[float] = None
    session_id: Optional[str] = None
    brand_kit: Optional[BrandKit] = None
    comments_processed: int = 0
    responses_spoken: int = 0
    is_live: bool = False

state = AppState()


# ── LIFESPAN ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    print("Synthcast API starting...")
    init_db()
    yield
    print("Synthcast API shutting down.")

app = FastAPI(
    title="Synthcast API",
    description="AI avatar agent engine for live stream interaction.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS (allow frontend + stream listener to call this) ──────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── BILLING ROUTES
app.include_router(billing_router)
from billing.auth_routes import router as auth_router
app.include_router(auth_router)
from billing.google_auth import router as google_auth_router
app.include_router(google_auth_router)
from api.creole_routes import router as creole_router
app.include_router(creole_router)
from api.waitlist_routes import router as waitlist_router
app.include_router(waitlist_router)
from billing.password_reset import router as password_reset_router
app.include_router(password_reset_router)

# ── REQUEST / RESPONSE MODELS ─────────────────────────────────────────────────

class BrandKitRequest(BaseModel):
    creator_name: str = Field(..., example="Alex Johnson")
    avatar_name: str = Field(..., example="AI Alex")
    personality: str = Field(..., example="confident, warm, direct, never sarcastic")
    tone_adjectives: list[str] = Field(default=["warm", "direct"])
    banned_topics: list[str] = Field(default=["politics", "religion"])
    banned_words: list[str] = Field(default=[])
    approved_topics: list[str] = Field(default=[])
    cta_scripts: list[str] = Field(default=["Follow for more!"])
    language: str = Field(default="en")
    humor_level: int = Field(default=5, ge=1, le=10)
    formality: int = Field(default=5, ge=1, le=10)
    max_response_words: int = Field(default=40, ge=10, le=100)


class StartSessionRequest(BaseModel):
    brand_kit: BrandKitRequest
    llm_provider: str = Field(default="openai", example="openai")
    memory_backend: str = Field(default="memory", example="memory")
    min_priority: int = Field(default=3, ge=0, le=10,
        description="Minimum priority to respond to (0=all, 3=low+, 7=high+)")


class CommentRequest(BaseModel):
    platform: str = Field(..., example="tiktok")
    username: str = Field(..., example="superfan99")
    text: str = Field(..., example="What camera do you use?")
    gift_value: float = Field(default=0.0, ge=0)
    is_new_follower: bool = Field(default=False)
    is_subscriber: bool = Field(default=False)


class AgentResponseOut(BaseModel):
    text: str
    tone: str
    should_speak: bool
    estimated_duration_s: float
    priority: int
    metadata: dict


class SessionStatus(BaseModel):
    is_live: bool
    session_id: Optional[str]
    session_duration_s: Optional[float]
    comments_processed: int
    responses_spoken: int
    queue_size: int
    memory_stats: Optional[dict]




# ── LEGAL PAGES ──────────────────────────────────────────────────────────────

@app.get("/privacy", response_class=HTMLResponse, tags=["legal"])
async def privacy_policy():
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Synthcast Privacy Policy</title>
<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.7}h1{font-size:2em;margin-bottom:8px}h2{margin-top:32px;font-size:1.2em}p,li{color:#333}a{color:#5B4FD4}.header{background:#07070A;color:white;padding:20px;border-radius:8px;margin-bottom:32px}.logo{font-size:1.4em;font-weight:700;letter-spacing:.05em}</style>
</head><body>
<div class="header"><div class="logo">SYNTHCAST</div><div style="font-size:.85em;opacity:.7;margin-top:4px">Privacy Policy</div></div>
<h1>Privacy Policy</h1>
<p><strong>Last updated: May 2026</strong></p>
<p>Synthcast ("we", "us", or "our") operates the Synthcast platform, an AI-powered live streaming service. This Privacy Policy explains how we collect, use, and protect your information.</p>
<h2>1. Information We Collect</h2>
<ul>
<li><strong>Account information:</strong> Name, email address, and username when you register.</li>
<li><strong>Stream data:</strong> Comments, usernames, and interactions from your live streams for the purpose of generating AI responses.</li>
<li><strong>API keys:</strong> Third-party API keys you provide (ElevenLabs, HeyGen, OpenAI) are stored encrypted and used solely to power your avatar.</li>
<li><strong>Usage data:</strong> Session duration, comment counts, and engagement metrics.</li>
</ul>
<h2>2. How We Use Your Information</h2>
<ul>
<li>To operate and improve the Synthcast platform.</li>
<li>To generate AI responses to your live stream viewers.</li>
<li>To process payments via Stripe.</li>
<li>To send service-related communications.</li>
</ul>
<h2>3. TikTok Data</h2>
<p>When you connect your TikTok account, Synthcast accesses live stream comment data via the TikTok Live API. This data is used in real time to generate responses and is not stored beyond your active session. We do not sell or share TikTok user data with third parties.</p>
<h2>4. Data Sharing</h2>
<p>We do not sell your personal data. We share data only with service providers necessary to operate Synthcast (Stripe for payments, Railway for hosting, OpenAI for AI responses).</p>
<h2>5. Data Security</h2>
<p>We implement industry-standard security measures to protect your data. API keys are stored encrypted. We use HTTPS for all communications.</p>
<h2>6. Your Rights</h2>
<p>You may request deletion of your account and associated data at any time by contacting us at privacy@synthcast.io.</p>
<h2>7. Contact</h2>
<p>For privacy questions: <a href="mailto:privacy@synthcast.io">privacy@synthcast.io</a></p>
</body></html>"""


@app.get("/terms", response_class=HTMLResponse, tags=["legal"])
async def terms_of_service():
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Synthcast Terms of Service</title>
<style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.7}h1{font-size:2em;margin-bottom:8px}h2{margin-top:32px;font-size:1.2em}p,li{color:#333}a{color:#5B4FD4}.header{background:#07070A;color:white;padding:20px;border-radius:8px;margin-bottom:32px}.logo{font-size:1.4em;font-weight:700;letter-spacing:.05em}</style>
</head><body>
<div class="header"><div class="logo">SYNTHCAST</div><div style="font-size:.85em;opacity:.7;margin-top:4px">Terms of Service</div></div>
<h1>Terms of Service</h1>
<p><strong>Last updated: May 2026</strong></p>
<p>By using Synthcast, you agree to these Terms of Service. Please read them carefully.</p>
<h2>1. Service Description</h2>
<p>Synthcast is an AI-powered live streaming platform that enables creators to deploy AI avatars that interact with their audience in real time. The service includes voice cloning, video avatar generation, and multi-platform streaming capabilities.</p>
<h2>2. Eligibility</h2>
<p>You must be at least 18 years old to use Synthcast. By using the service, you confirm you meet this requirement.</p>
<h2>3. Acceptable Use</h2>
<p>You agree not to use Synthcast to:</p>
<ul>
<li>Violate any applicable laws or regulations.</li>
<li>Impersonate others without consent.</li>
<li>Generate harmful, abusive, or illegal content.</li>
<li>Violate TikTok, Twitch, YouTube, or Instagram terms of service.</li>
<li>Attempt to reverse engineer or circumvent platform security.</li>
</ul>
<h2>4. Subscriptions and Billing</h2>
<p>Synthcast offers Free, Creator ($29/mo), and Pro ($79/mo) plans. Subscriptions are billed monthly via Stripe. You may cancel at any time. Refunds are handled on a case-by-case basis.</p>
<h2>5. AI-Generated Content</h2>
<p>You are responsible for all content generated by your AI avatar. Synthcast provides the technology but does not control or moderate AI-generated responses in real time.</p>
<h2>6. API Keys and Third-Party Services</h2>
<p>Free and Creator tier users provide their own API keys. You are responsible for compliance with the terms of those third-party services (ElevenLabs, HeyGen, OpenAI).</p>
<h2>7. Termination</h2>
<p>We reserve the right to suspend or terminate accounts that violate these terms.</p>
<h2>8. Limitation of Liability</h2>
<p>Synthcast is provided "as is" without warranty. We are not liable for damages arising from use of the service.</p>
<h2>9. Contact</h2>
<p>For legal questions: <a href="mailto:legal@synthcast.io">legal@synthcast.io</a></p>
</body></html>"""


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "is_live": state.is_live,
        "timestamp": time.time(),
    }


# ── SESSION MANAGEMENT ────────────────────────────────────────────────────────

@app.post("/session/start", tags=["session"])
async def start_session(req: StartSessionRequest):
    """
    Initialize a live stream session.
    Call this before going live. Sets up the engine, brand kit, and queue.
    """
    if state.is_live:
        raise HTTPException(400, "Session already active. Call /session/stop first.")

    # Build brand kit
    kit = BrandKit(
        creator_name=req.brand_kit.creator_name,
        avatar_name=req.brand_kit.avatar_name,
        personality=req.brand_kit.personality,
        tone_adjectives=req.brand_kit.tone_adjectives,
        banned_topics=req.brand_kit.banned_topics,
        banned_words=req.brand_kit.banned_words,
        approved_topics=req.brand_kit.approved_topics,
        cta_scripts=req.brand_kit.cta_scripts,
        language=req.brand_kit.language,
        humor_level=req.brand_kit.humor_level,
        formality=req.brand_kit.formality,
        max_response_words=req.brand_kit.max_response_words,
    )

    # Build engine
    llm_client = get_llm_client(req.llm_provider)
    memory_store = get_memory_store(req.memory_backend)

    min_priority = Priority(req.min_priority) if req.min_priority in [p.value for p in Priority] else Priority.LOW

    state.engine = ResponseEngine(
        brand_kit=kit,
        llm_client=llm_client,
        memory_store=memory_store,
        min_priority=min_priority,
    )
    state.queue = CommentQueue(max_size=500)
    state.brand_kit = kit
    state.session_start = time.time()
    state.session_id = f"session_{int(state.session_start)}"
    state.comments_processed = 0
    state.responses_spoken = 0
    state.is_live = True

    return {
        "session_id": state.session_id,
        "status": "live",
        "creator": kit.creator_name,
        "avatar": kit.avatar_name,
        "llm_provider": req.llm_provider,
        "started_at": state.session_start,
    }


@app.post("/session/stop", tags=["session"])
async def stop_session():
    """End the current live session and return final stats."""
    if not state.is_live:
        raise HTTPException(400, "No active session.")

    duration = time.time() - state.session_start if state.session_start else 0
    mem_stats = state.engine.memory.get_stats() if state.engine and state.engine.memory else {}
    queue_stats = state.queue.stats() if state.queue else {}

    final = {
        "session_id": state.session_id,
        "duration_s": round(duration, 1),
        "duration_min": round(duration / 60, 1),
        "comments_processed": state.comments_processed,
        "responses_spoken": state.responses_spoken,
        "memory": mem_stats,
        "queue": queue_stats,
    }

    # Reset state
    state.engine = None
    state.queue = None
    state.session_start = None
    state.session_id = None
    state.is_live = False

    return final


@app.get("/session/status", response_model=SessionStatus, tags=["session"])
async def session_status():
    """Get current session stats. Poll this from the dashboard."""
    duration = time.time() - state.session_start if state.session_start else None
    mem_stats = state.engine.memory.get_stats() if state.engine and state.engine.memory else None
    queue_size = state.queue.size() if state.queue else 0

    return SessionStatus(
        is_live=state.is_live,
        session_id=state.session_id,
        session_duration_s=round(duration, 1) if duration else None,
        comments_processed=state.comments_processed,
        responses_spoken=state.responses_spoken,
        queue_size=queue_size,
        memory_stats=mem_stats,
    )


# ── CORE COMMENT PROCESSING ───────────────────────────────────────────────────

def _require_session():
    """Dependency: raises 400 if no active session."""
    if not state.is_live or not state.engine:
        raise HTTPException(400, "No active session. Call /session/start first.")
    return state.engine


@app.post("/comment/process", response_model=Optional[AgentResponseOut], tags=["agent"])
async def process_comment(
    req: CommentRequest,
    engine: ResponseEngine = Depends(_require_session),
):
    """
    Process a single live comment synchronously.
    Returns the agent response, or null if the comment was skipped
    (spam, toxic, low priority, banned topic).

    This is the primary endpoint called by the stream listener.
    """
    comment = IncomingComment(
        platform=req.platform,
        username=req.username,
        text=req.text,
        gift_value=req.gift_value,
        is_new_follower=req.is_new_follower,
        is_subscriber=req.is_subscriber,
    )

    # Run in thread pool to avoid blocking the event loop during LLM call
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, engine.process, comment)

    state.comments_processed += 1

    if response is None:
        return None

    state.responses_spoken += 1
    return AgentResponseOut(**response.to_dict())


@app.post("/comment/batch", tags=["agent"])
async def process_batch(
    comments: list[CommentRequest],
    engine: ResponseEngine = Depends(_require_session),
):
    """
    Process multiple comments in sequence.
    Useful for replaying a backlog when the stream resumes.
    Returns list of responses (None entries = skipped comments).
    """
    results = []
    loop = asyncio.get_event_loop()

    for req in comments[:20]:  # cap at 20 per batch
        comment = IncomingComment(
            platform=req.platform,
            username=req.username,
            text=req.text,
            gift_value=req.gift_value,
            is_new_follower=req.is_new_follower,
            is_subscriber=req.is_subscriber,
        )
        response = await loop.run_in_executor(None, engine.process, comment)
        state.comments_processed += 1

        if response:
            state.responses_spoken += 1
            results.append(response.to_dict())
        else:
            results.append(None)

    return {"processed": len(results), "responses": results}


# ── BRAND KIT ─────────────────────────────────────────────────────────────────

@app.get("/brand-kit", tags=["config"])
async def get_brand_kit():
    """Return the active brand kit configuration."""
    if not state.brand_kit:
        raise HTTPException(400, "No active session.")
    kit = state.brand_kit
    return {
        "creator_name": kit.creator_name,
        "avatar_name": kit.avatar_name,
        "personality": kit.personality,
        "banned_topics": kit.banned_topics,
        "banned_words": kit.banned_words,
        "humor_level": kit.humor_level,
        "formality": kit.formality,
        "max_response_words": kit.max_response_words,
        "language": kit.language,
    }


@app.patch("/brand-kit/banned-topics", tags=["config"])
async def update_banned_topics(topics: list[str]):
    """Hot-update banned topics mid-stream without restarting the session."""
    if not state.engine:
        raise HTTPException(400, "No active session.")
    state.engine.brand_kit.banned_topics = topics
    state.brand_kit.banned_topics = topics
    return {"banned_topics": topics}


@app.patch("/brand-kit/cta", tags=["config"])
async def update_cta(scripts: list[str]):
    """Hot-swap CTA scripts mid-stream."""
    if not state.engine:
        raise HTTPException(400, "No active session.")
    state.engine.brand_kit.cta_scripts = scripts
    state.brand_kit.cta_scripts = scripts
    return {"cta_scripts": scripts}


# ── VIEWER MEMORY ─────────────────────────────────────────────────────────────

@app.get("/memory/viewer/{platform}/{username}", tags=["memory"])
async def get_viewer(platform: str, username: str):
    """Look up a specific viewer's profile."""
    if not state.engine or not state.engine.memory:
        raise HTTPException(400, "No active session or memory store.")
    viewer = state.engine.memory.get_viewer(platform, username)
    if not viewer:
        raise HTTPException(404, f"No memory for {username} on {platform}.")
    from dataclasses import asdict
    return asdict(viewer)


@app.get("/memory/stats", tags=["memory"])
async def memory_stats():
    """Aggregated audience stats for the dashboard."""
    if not state.engine or not state.engine.memory:
        raise HTTPException(400, "No active session.")
    return state.engine.memory.get_stats()


@app.post("/memory/viewer/{platform}/{username}/fact", tags=["memory"])
async def add_viewer_fact(platform: str, username: str, fact: str):
    """
    Manually tag a viewer with a notable fact.
    e.g. POST /memory/viewer/tiktok/superfan99/fact?fact=loves+cooking
    The agent will reference this in future responses to that viewer.
    """
    if not state.engine or not state.engine.memory:
        raise HTTPException(400, "No active session.")
    state.engine.memory.add_fact(platform, username, fact)
    return {"username": username, "fact": fact, "status": "added"}


# ── QUEUE ─────────────────────────────────────────────────────────────────────

@app.get("/queue/stats", tags=["queue"])
async def queue_stats():
    """Current queue depth and throughput metrics."""
    if not state.queue:
        raise HTTPException(400, "No active session.")
    return state.queue.stats()


@app.delete("/queue/clear", tags=["queue"])
async def clear_queue():
    """
    Drain the comment queue.
    Use when the stream is overwhelmed — drops all queued comments
    and starts fresh. Gifts already processed are unaffected.
    """
    if not state.queue:
        raise HTTPException(400, "No active session.")
    size_before = state.queue.size()
    state.queue.clear()
    return {"cleared": size_before, "queue_size": 0}




# ── CREATOR PROFILES ──────────────────────────────────────────────────────────

@app.get("/creator/profile/{username}", tags=["creators"])
async def get_creator_profile(username: str):
    """Get public creator profile by username/handle."""
    from billing.auth_routes import _users

    # Search by handle or email prefix
    user = None
    for u in _users.values():
        handle = u.get("creator_handle", "").lstrip("@").lower()
        email_prefix = u.get("email", "").split("@")[0].lower()
        creator_id = u.get("creator_id", "").lower()

        if (handle == username.lower() or
            email_prefix == username.lower() or
            creator_id.startswith(username.lower())):
            user = u
            break

    if not user:
        raise HTTPException(404, "Creator not found.")

    return {
        "name": user.get("name", "Creator"),
        "handle": "@" + username,
        "bio": user.get("bio", "AI-powered creator on Synthcast."),
        "is_live": state.is_live,
        "streams": 0,
        "comments_handled": state.comments_processed if state.is_live else 0,
        "platforms": ["TikTok", "Twitch", "YouTube"],
        "tier": user.get("tier", "free"),
        "photo_url": user.get("photo_url", None),
    }

# ── TEST ENDPOINT ─────────────────────────────────────────────────────────────

@app.post("/test/comment", tags=["dev"])
async def test_comment(text: str = "What's your favorite game?", username: str = "testuser"):
    """
    Quick test endpoint — no active session needed.
    Uses MockLLMClient and default brand kit.
    Great for checking the server is alive and the engine works.
    """
    from agent.llm_clients import MockLLMClient

    kit = BrandKit.default()
    engine = ResponseEngine(
        brand_kit=kit,
        llm_client=MockLLMClient(),
        memory_store=get_memory_store("memory"),
    )
    comment = IncomingComment(platform="test", username=username, text=text)
    response = engine.process(comment)

    if not response:
        return {"result": "skipped", "reason": "comment filtered"}

    return {
        "input": text,
        "username": username,
        "response": response.to_dict(),
        "note": "Using MockLLMClient — swap to openai for real responses",
    }
