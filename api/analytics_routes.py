"""
api/analytics_routes.py
Creator analytics endpoints for Synthcast.

Endpoints:
  POST /analytics/session        — save a completed session
  GET  /analytics/sessions       — get session history
  GET  /analytics/summary        — lifetime stats
  GET  /analytics/top-viewers    — most active viewers
"""

import os
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Text, Boolean
try:
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/analytics", tags=["analytics"])

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///synthcast.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class StreamSession(Base):
    __tablename__ = "stream_sessions"
    id                  = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_id          = Column(String, nullable=False, index=True)
    started_at          = Column(DateTime, nullable=True)
    ended_at            = Column(DateTime, nullable=True)
    duration_seconds    = Column(Integer, default=0)
    platforms           = Column(String, default="YouTube")
    comments_processed  = Column(Integer, default=0)
    responses_spoken    = Column(Integer, default=0)
    unique_viewers      = Column(Integer, default=0)
    gifts_total         = Column(Float, default=0.0)
    top_comment         = Column(Text, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)


class ViewerRecord(Base):
    __tablename__ = "viewer_records"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    creator_id  = Column(String, nullable=False, index=True)
    username    = Column(String, nullable=False)
    platform    = Column(String, default="YouTube")
    comments    = Column(Integer, default=0)
    gifts_total = Column(Float, default=0.0)
    first_seen  = Column(DateTime, default=datetime.utcnow)
    last_seen   = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

# Migration — add missing columns if they don't exist
def run_migrations():
    try:
        with engine.connect() as conn:
            # Add columns that may be missing from older table
            migrations = [
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS duration_seconds INTEGER DEFAULT 0",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS platforms VARCHAR DEFAULT 'YouTube'",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS comments_processed INTEGER DEFAULT 0",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS responses_spoken INTEGER DEFAULT 0",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS unique_viewers INTEGER DEFAULT 0",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS gifts_total FLOAT DEFAULT 0.0",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS top_comment TEXT",
                "ALTER TABLE stream_sessions ALTER COLUMN platform DROP NOT NULL",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS started_at TIMESTAMP",
                "ALTER TABLE stream_sessions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP",
            ]
            for sql in migrations:
                try:
                    conn.execute(text(sql))
                    conn.commit()
                except Exception as e:
                    pass  # Column already exists
    except Exception as e:
        print(f"[Analytics] Migration note: {e}")

from sqlalchemy import text
run_migrations()


class SessionRequest(BaseModel):
    creator_id: str
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    duration_seconds: int = 0
    platforms: List[str] = ["YouTube"]
    comments_processed: int = 0
    responses_spoken: int = 0
    unique_viewers: int = 0
    gifts_total: float = 0.0
    top_comment: Optional[str] = None
    viewers: Optional[List[dict]] = None  # [{username, comments, gifts}]


@router.post("/session")
async def save_session(req: SessionRequest):
    """Save a completed stream session."""
    with SessionLocal() as db:
        session = StreamSession(
            creator_id=req.creator_id,
            started_at=datetime.fromtimestamp(req.started_at) if req.started_at else None,
            ended_at=datetime.fromtimestamp(req.ended_at) if req.ended_at else None,
            duration_seconds=req.duration_seconds,
            platforms=",".join(req.platforms),
            comments_processed=req.comments_processed,
            responses_spoken=req.responses_spoken,
            unique_viewers=req.unique_viewers,
            gifts_total=req.gifts_total,
            top_comment=req.top_comment,
        )
        db.add(session)

        # Update viewer records
        if req.viewers:
            for v in req.viewers:
                existing = db.query(ViewerRecord).filter(
                    ViewerRecord.creator_id == req.creator_id,
                    ViewerRecord.username == v.get("username")
                ).first()
                if existing:
                    existing.comments += v.get("comments", 0)
                    existing.gifts_total += v.get("gifts", 0.0)
                    existing.last_seen = datetime.utcnow()
                else:
                    db.add(ViewerRecord(
                        creator_id=req.creator_id,
                        username=v.get("username"),
                        platform=v.get("platform", "YouTube"),
                        comments=v.get("comments", 0),
                        gifts_total=v.get("gifts", 0.0),
                    ))

        db.commit()

    return {"status": "saved", "session_id": session.id}


@router.get("/sessions")
async def get_sessions(creator_id: str, limit: int = 20):
    """Get session history for a creator."""
    with SessionLocal() as db:
        sessions = db.query(StreamSession).filter(
            StreamSession.creator_id == creator_id
        ).order_by(StreamSession.created_at.desc()).limit(limit).all()

        return {
            "creator_id": creator_id,
            "count": len(sessions),
            "sessions": [
                {
                    "id": s.id,
                    "date": s.started_at.isoformat() if s.started_at else None,
                    "duration_seconds": s.duration_seconds,
                    "duration_formatted": f"{s.duration_seconds//3600}h {(s.duration_seconds%3600)//60}m" if s.duration_seconds >= 3600 else f"{s.duration_seconds//60}m {s.duration_seconds%60}s",
                    "platforms": s.platforms.split(",") if s.platforms else [],
                    "comments": s.comments_processed,
                    "responses": s.responses_spoken,
                    "viewers": s.unique_viewers,
                    "gifts": round(s.gifts_total, 2),
                    "response_rate": round(s.responses_spoken / max(s.comments_processed, 1) * 100, 1),
                }
                for s in sessions
            ]
        }


@router.get("/summary")
async def get_summary(creator_id: str):
    """Get lifetime analytics summary."""
    with SessionLocal() as db:
        sessions = db.query(StreamSession).filter(
            StreamSession.creator_id == creator_id
        ).all()

        if not sessions:
            return {
                "creator_id": creator_id,
                "total_sessions": 0,
                "total_hours": 0,
                "total_comments": 0,
                "total_responses": 0,
                "total_viewers": 0,
                "total_gifts": 0,
                "avg_response_rate": 0,
                "avg_session_duration": 0,
                "best_session": None,
            }

        total_duration = sum(s.duration_seconds for s in sessions)
        total_comments = sum(s.comments_processed for s in sessions)
        total_responses = sum(s.responses_spoken for s in sessions)
        total_viewers = sum(s.unique_viewers for s in sessions)
        total_gifts = sum(s.gifts_total for s in sessions)

        best = max(sessions, key=lambda s: s.comments_processed)

        return {
            "creator_id": creator_id,
            "total_sessions": len(sessions),
            "total_hours": round(total_duration / 3600, 1),
            "total_comments": total_comments,
            "total_responses": total_responses,
            "total_viewers": total_viewers,
            "total_gifts": round(total_gifts, 2),
            "avg_response_rate": round(total_responses / max(total_comments, 1) * 100, 1),
            "avg_session_duration": round(total_duration / len(sessions) / 60, 1),
            "best_session": {
                "date": best.started_at.isoformat() if best.started_at else None,
                "comments": best.comments_processed,
                "viewers": best.unique_viewers,
            }
        }


@router.get("/top-viewers")
async def get_top_viewers(creator_id: str, limit: int = 10):
    """Get most active viewers for a creator."""
    with SessionLocal() as db:
        viewers = db.query(ViewerRecord).filter(
            ViewerRecord.creator_id == creator_id
        ).order_by(ViewerRecord.comments.desc()).limit(limit).all()

        return {
            "creator_id": creator_id,
            "viewers": [
                {
                    "username": v.username,
                    "platform": v.platform,
                    "comments": v.comments,
                    "gifts": round(v.gifts_total, 2),
                    "first_seen": v.first_seen.isoformat() if v.first_seen else None,
                    "last_seen": v.last_seen.isoformat() if v.last_seen else None,
                }
                for v in viewers
            ]
        }
