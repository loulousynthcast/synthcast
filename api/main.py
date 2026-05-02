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
from fastapi.responses import JSONResponse
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
try:
    from billing.routes import router as billing_router
    from database import init_db
    BILLING_ENABLED = True
except Exception as e:
    print(f"WARNING: Billing/DB failed to load: {e}")
    billing_router = None
    BILLING_ENABLED = False
    def init_db(): pass
if billing_router:
    app.include_router(billing_router)

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


# ── TEST ENDPOINT ─────────────────────────────────────────────────────────────

@app.get("/test/billing", tags=["dev"])
async def test_billing():
    """Check if billing module loaded correctly."""
    try:
        from billing.stripe_billing import TIER_CONFIGS, CreatorTier
        from database.models import Base
        return {
            "billing": "loaded",
            "tiers": list(TIER_CONFIGS.keys()),
            "database": "loaded"
        }
    except Exception as e:
        return {"billing": "failed", "error": str(e)}

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
