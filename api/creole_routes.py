"""
api/creole_routes.py
API endpoints for the Haitian Creole voice recording portal.

Endpoints:
  POST /creole/recording      — submit a voice recording
  POST /creole/suggestion     — submit sentence suggestions
  GET  /creole/stats          — get recording counts
  GET  /creole/sentences      — get approved sentences for recording
  GET  /creole/review         — get pending submissions (admin)
  POST /creole/approve/{id}   — approve a suggestion (admin)
"""

import os
import uuid
import hashlib
import time
from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, Float, DateTime, LargeBinary, Integer, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/creole", tags=["creole"])

# ── DATABASE ──────────────────────────────────────────────────────────────────
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


class CreoleRecording(Base):
    __tablename__ = "creole_recordings"
    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sentence_text    = Column(Text, nullable=False)
    sentence_original = Column(Text, nullable=True)
    sentence_english = Column(Text, nullable=True)
    category         = Column(String, nullable=True)
    was_corrected    = Column(Boolean, default=False)
    audio_size_bytes = Column(Integer, nullable=True)
    audio_format     = Column(String, default="webm")
    duration_s       = Column(Float, nullable=True)
    contributor_id   = Column(String, nullable=True)
    ip_hash          = Column(String, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    reviewed         = Column(Boolean, default=False)
    approved         = Column(Boolean, default=False)


class CreoleSuggestion(Base):
    __tablename__ = "creole_suggestions"
    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    topic            = Column(String, nullable=False)
    sentence_creole  = Column(Text, nullable=False)
    sentence_english = Column(Text, nullable=True)
    contributor_id   = Column(String, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    reviewed         = Column(Boolean, default=False)
    approved         = Column(Boolean, default=False)
    added_to_pool    = Column(Boolean, default=False)


# Create tables
Base.metadata.create_all(bind=engine)


# ── MODELS ────────────────────────────────────────────────────────────────────
class RecordingSubmission(BaseModel):
    sentence_text: str
    sentence_original: Optional[str] = None
    sentence_english: Optional[str] = None
    category: Optional[str] = None
    was_corrected: bool = False
    duration_s: Optional[float] = None
    contributor_id: Optional[str] = None


class SuggestionItem(BaseModel):
    creole: str
    english: Optional[str] = None


class SuggestionSubmission(BaseModel):
    topic: str
    sentences: List[SuggestionItem]
    contributor_id: Optional[str] = None


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@router.post("/recording")
async def submit_recording(req: RecordingSubmission, request: Request):
    """Submit a voice recording metadata."""
    # Hash IP for privacy
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    with SessionLocal() as db:
        recording = CreoleRecording(
            sentence_text=req.sentence_text,
            sentence_original=req.sentence_original,
            sentence_english=req.sentence_english,
            category=req.category,
            was_corrected=req.was_corrected,
            duration_s=req.duration_s,
            contributor_id=req.contributor_id or str(uuid.uuid4()),
            ip_hash=ip_hash,
        )
        db.add(recording)
        db.commit()
        db.refresh(recording)
        recording_id = recording.id

        total = db.query(CreoleRecording).count()
        contributors = db.query(CreoleRecording.ip_hash).distinct().count()

    return {
        "status": "received",
        "recording_id": recording_id,
        "total_recordings": total,
        "total_contributors": contributors,
        "message": "Mèsi! Anrejistreman ou a resevwa.",
    }


@router.post("/suggestion")
async def submit_suggestion(req: SuggestionSubmission, request: Request):
    """Submit community sentence suggestions for review."""
    client_ip = request.client.host if request.client else "unknown"
    contributor_id = req.contributor_id or hashlib.sha256(client_ip.encode()).hexdigest()[:12]

    saved = 0
    with SessionLocal() as db:
        for item in req.sentences[:10]:  # max 10
            if not item.creole.strip():
                continue
            suggestion = CreoleSuggestion(
                topic=req.topic,
                sentence_creole=item.creole.strip(),
                sentence_english=item.english.strip() if item.english else None,
                contributor_id=contributor_id,
            )
            db.add(suggestion)
            saved += 1
        db.commit()

    return {
        "status": "received",
        "sentences_saved": saved,
        "message": f"Mèsi! {saved} fraz voye bay ekip la pou revizyon.",
    }


@router.get("/stats")
async def get_stats():
    """Get recording and contributor counts for the portal counter."""
    with SessionLocal() as db:
        total_recordings = db.query(CreoleRecording).count()
        total_contributors = db.query(CreoleRecording.ip_hash).distinct().count()
        total_suggestions = db.query(CreoleSuggestion).count()
        pending_review = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.reviewed == False
        ).count()

    return {
        "total_recordings": total_recordings,
        "total_contributors": total_contributors,
        "total_suggestions": total_suggestions,
        "pending_review": pending_review,
    }


@router.get("/review")
async def get_pending_suggestions(admin_key: str = ""):
    """Get suggestions pending review. Requires admin key."""
    if admin_key != os.getenv("ADMIN_KEY", "synthcast_admin_2026"):
        raise HTTPException(403, "Invalid admin key.")

    with SessionLocal() as db:
        pending = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.reviewed == False
        ).order_by(CreoleSuggestion.created_at).all()

        return {
            "count": len(pending),
            "suggestions": [
                {
                    "id": s.id,
                    "topic": s.topic,
                    "creole": s.sentence_creole,
                    "english": s.sentence_english,
                    "submitted": s.created_at.isoformat() if s.created_at else None,
                }
                for s in pending
            ]
        }


@router.post("/approve/{suggestion_id}")
async def approve_suggestion(suggestion_id: str, admin_key: str = ""):
    """Approve a suggestion and add it to the recording pool."""
    if admin_key != os.getenv("ADMIN_KEY", "synthcast_admin_2026"):
        raise HTTPException(403, "Invalid admin key.")

    with SessionLocal() as db:
        suggestion = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.id == suggestion_id
        ).first()
        if not suggestion:
            raise HTTPException(404, "Suggestion not found.")

        suggestion.reviewed = True
        suggestion.approved = True
        suggestion.added_to_pool = True
        db.commit()

    return {
        "status": "approved",
        "sentence": suggestion.sentence_creole,
        "topic": suggestion.topic,
    }


@router.post("/reject/{suggestion_id}")
async def reject_suggestion(suggestion_id: str, admin_key: str = ""):
    """Reject a suggestion."""
    if admin_key != os.getenv("ADMIN_KEY", "synthcast_admin_2026"):
        raise HTTPException(403, "Invalid admin key.")

    with SessionLocal() as db:
        suggestion = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.id == suggestion_id
        ).first()
        if not suggestion:
            raise HTTPException(404, "Suggestion not found.")
        suggestion.reviewed = True
        suggestion.approved = False
        db.commit()

    return {"status": "rejected"}


class EditSuggestionRequest(BaseModel):
    creole: Optional[str] = None
    english: Optional[str] = None


@router.post("/edit/{suggestion_id}")
async def edit_suggestion(suggestion_id: str, req: EditSuggestionRequest, admin_key: str = ""):
    """Edit a suggestion before approving."""
    if admin_key != os.getenv("ADMIN_KEY", "synthcast_admin_2026"):
        raise HTTPException(403, "Invalid admin key.")

    with SessionLocal() as db:
        suggestion = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.id == suggestion_id
        ).first()
        if not suggestion:
            raise HTTPException(404, "Suggestion not found.")

        if req.creole:
            suggestion.sentence_creole = req.creole.strip()
        if req.english is not None:
            suggestion.sentence_english = req.english.strip() if req.english else None
        db.commit()
        
        return {
            "status": "updated",
            "creole": suggestion.sentence_creole,
            "english": suggestion.sentence_english,
        }


@router.get("/approved-sentences")
async def get_approved_sentences():
    """Get community-approved sentences to add to recording pool."""
    with SessionLocal() as db:
        approved = db.query(CreoleSuggestion).filter(
            CreoleSuggestion.added_to_pool == True
        ).all()

        return {
            "count": len(approved),
            "sentences": [
                {
                    "creole": s.sentence_creole,
                    "english": s.sentence_english,
                    "category": s.topic,
                }
                for s in approved
            ]
        }
