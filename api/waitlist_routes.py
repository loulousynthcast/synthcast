"""
api/waitlist_routes.py
Waitlist email collection for Synthcast landing page.

Endpoints:
  POST /waitlist          — submit email to waitlist
  GET  /waitlist/stats    — total waitlist count
  GET  /waitlist/list     — get all emails (admin only)
"""

import os
import uuid
import hashlib
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/waitlist", tags=["waitlist"])

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


class WaitlistEntry(Base):
    __tablename__ = "waitlist"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email        = Column(String, unique=True, nullable=False, index=True)
    name         = Column(String, nullable=True)
    source       = Column(String, default="landing")  # landing, tiktok, creole, etc
    ip_hash      = Column(String, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    notified     = Column(Boolean, default=False)
    converted    = Column(Boolean, default=False)  # True when they sign up


Base.metadata.create_all(bind=engine)


class WaitlistRequest(BaseModel):
    email: str
    name: Optional[str] = None
    source: Optional[str] = "landing"


@router.post("")
async def join_waitlist(req: WaitlistRequest, request: Request):
    """Add email to waitlist."""
    email = req.email.lower().strip()

    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address.")

    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    with SessionLocal() as db:
        # Check if already exists
        existing = db.query(WaitlistEntry).filter(WaitlistEntry.email == email).first()
        if existing:
            total = db.query(WaitlistEntry).count()
            return {
                "status": "already_registered",
                "message": "You are already on the waitlist!",
                "total": total,
            }

        entry = WaitlistEntry(
            email=email,
            name=req.name,
            source=req.source or "landing",
            ip_hash=ip_hash,
        )
        db.add(entry)
        db.commit()
        total = db.query(WaitlistEntry).count()

    # Send confirmation email
    try:
        from billing.email_service import send_waitlist_confirmation
        send_waitlist_confirmation(email, total)
    except Exception as e:
        print(f"[Waitlist] Confirmation email failed: {e}")

    return {
        "status": "joined",
        "message": "You are on the list! We will be in touch.",
        "position": total,
        "total": total,
    }


@router.get("/stats")
async def waitlist_stats():
    """Get waitlist count."""
    with SessionLocal() as db:
        total = db.query(WaitlistEntry).count()
        converted = db.query(WaitlistEntry).filter(WaitlistEntry.converted == True).count()
        by_source = {}
        for source in ["landing", "tiktok", "creole", "other"]:
            by_source[source] = db.query(WaitlistEntry).filter(
                WaitlistEntry.source == source
            ).count()

    return {
        "total": total,
        "converted": converted,
        "conversion_rate": round(converted / total * 100, 1) if total > 0 else 0,
        "by_source": by_source,
    }


@router.get("/list")
async def get_waitlist(admin_key: str = ""):
    """Get all waitlist emails. Admin only."""
    if admin_key != os.getenv("ADMIN_KEY", "synthcast_admin_2026"):
        raise HTTPException(403, "Invalid admin key.")

    with SessionLocal() as db:
        entries = db.query(WaitlistEntry).order_by(WaitlistEntry.created_at.desc()).all()
        return {
            "total": len(entries),
            "entries": [
                {
                    "email": e.email,
                    "name": e.name,
                    "source": e.source,
                    "joined": e.created_at.isoformat() if e.created_at else None,
                    "converted": e.converted,
                }
                for e in entries
            ]
        }
