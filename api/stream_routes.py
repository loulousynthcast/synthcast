"""
api/stream_routes.py
Real-time comment feed endpoints for Synthcast dashboard.

Endpoints:
  GET  /stream/status           — get current live status + recent comments
  POST /stream/start            — mark session as live
  POST /stream/stop             — mark session as offline
  GET  /stream/comments         — get recent comments (polling)
  POST /stream/comment          — add a comment (from platform listeners)
"""

import os
import time
import uuid
from typing import Optional, List
from collections import deque

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/stream", tags=["stream"])

# In-memory comment buffer — last 100 comments per session
# Resets on redeploy (acceptable — live data is ephemeral)
_sessions = {}  # creator_id -> session state
_comment_buffer = {}  # creator_id -> deque of comments
MAX_COMMENTS = 100


class StreamSession:
    def __init__(self, creator_id: str):
        self.creator_id = creator_id
        self.is_live = False
        self.started_at = None
        self.stopped_at = None
        self.platforms = []
        self.comments_processed = 0
        self.responses_spoken = 0
        self.unique_viewers = set()
        self.gifts_total = 0.0


class Comment(BaseModel):
    username: str
    text: str
    platform: str = "unknown"
    comment_type: str = "C"  # C=comment, Q=question, G=gift, F=follow, S=skip
    response: Optional[str] = None
    gift_amount: Optional[float] = None
    timestamp: Optional[float] = None


class StartRequest(BaseModel):
    creator_id: str
    platforms: List[str] = ["YouTube"]


class StopRequest(BaseModel):
    creator_id: str


def _get_session(creator_id: str) -> StreamSession:
    if creator_id not in _sessions:
        _sessions[creator_id] = StreamSession(creator_id)
    return _sessions[creator_id]


def _get_buffer(creator_id: str) -> deque:
    if creator_id not in _comment_buffer:
        _comment_buffer[creator_id] = deque(maxlen=MAX_COMMENTS)
    return _comment_buffer[creator_id]


@router.post("/start")
async def start_stream(req: StartRequest):
    """Mark a creator's session as live."""
    session = _get_session(req.creator_id)
    session.is_live = True
    session.started_at = time.time()
    session.platforms = req.platforms
    session.comments_processed = 0
    session.responses_spoken = 0
    session.unique_viewers = set()
    session.gifts_total = 0.0
    _get_buffer(req.creator_id).clear()

    return {
        "status": "live",
        "creator_id": req.creator_id,
        "platforms": req.platforms,
        "started_at": session.started_at,
    }


@router.post("/stop")
async def stop_stream(req: StopRequest):
    """Mark a creator's session as offline."""
    session = _get_session(req.creator_id)
    session.is_live = False
    session.stopped_at = time.time()
    duration = (session.stopped_at - (session.started_at or session.stopped_at))

    return {
        "status": "offline",
        "creator_id": req.creator_id,
        "duration_seconds": int(duration),
        "comments_processed": session.comments_processed,
        "responses_spoken": session.responses_spoken,
        "unique_viewers": len(session.unique_viewers),
        "gifts_total": session.gifts_total,
    }


@router.get("/status")
async def get_status(creator_id: str):
    """Get current live status for a creator."""
    session = _get_session(creator_id)
    buffer = _get_buffer(creator_id)

    duration = 0
    if session.is_live and session.started_at:
        duration = int(time.time() - session.started_at)

    return {
        "is_live": session.is_live,
        "creator_id": creator_id,
        "platforms": session.platforms,
        "started_at": session.started_at,
        "duration_seconds": duration,
        "comments_processed": session.comments_processed,
        "responses_spoken": session.responses_spoken,
        "unique_viewers": len(session.unique_viewers),
        "gifts_total": round(session.gifts_total, 2),
        "recent_comments": list(buffer)[-20:],  # last 20
    }


@router.get("/comments")
async def get_comments(creator_id: str, since: float = 0):
    """Get comments since a given timestamp — for polling."""
    buffer = _get_buffer(creator_id)
    comments = [c for c in buffer if c.get("timestamp", 0) > since]
    return {
        "creator_id": creator_id,
        "comments": comments,
        "count": len(comments),
        "server_time": time.time(),
    }


@router.post("/comment")
async def add_comment(creator_id: str, comment: Comment):
    """Add a comment to the buffer — called by platform listeners."""
    session = _get_session(creator_id)
    buffer = _get_buffer(creator_id)

    c = comment.dict()
    c["id"] = str(uuid.uuid4())[:8]
    c["timestamp"] = comment.timestamp or time.time()

    buffer.append(c)
    session.comments_processed += 1
    session.unique_viewers.add(comment.username)

    if comment.response:
        session.responses_spoken += 1
    if comment.gift_amount:
        session.gifts_total += comment.gift_amount

    return {"status": "added", "id": c["id"]}


@router.get("/all-status")
async def get_all_status():
    """Get live status for all creators — admin view."""
    live = []
    for creator_id, session in _sessions.items():
        if session.is_live:
            live.append({
                "creator_id": creator_id,
                "platforms": session.platforms,
                "duration": int(time.time() - (session.started_at or time.time())),
                "comments": session.comments_processed,
            })
    return {"live_count": len(live), "sessions": live}
