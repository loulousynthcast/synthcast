"""
synthcast/database/models.py

PostgreSQL database models using SQLAlchemy ORM.
Stores creator accounts, viewer memory, sessions, and analytics permanently.

Phase 1: SQLite (no setup needed — works locally right now)
Phase 2: PostgreSQL (for production deployment)

The same code works for both — just change DATABASE_URL in .env.

Install:
    pip install sqlalchemy alembic psycopg2-binary aiosqlite

YOUR PART:
  Local dev (works right now, no setup):
    DATABASE_URL=sqlite:///synthcast.db

  Production PostgreSQL (Railway, Render, Supabase — all free tiers):
    DATABASE_URL=postgresql://user:password@host:5432/synthcast
"""

import os
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    create_engine, Column, String, Float, Integer,
    Boolean, DateTime, Text, ForeignKey, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import (
    DeclarativeBase, relationship,
    sessionmaker, Session
)
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv

load_dotenv()


# ── DATABASE SETUP ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///synthcast.db"   # Default: local SQLite, works with zero setup
)

# SQLite needs special config for multi-thread use
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
    }
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        poolclass=StaticPool,
    )
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── MODELS ────────────────────────────────────────────────────────────────────

class Creator(Base):
    """A Synthcast creator account."""
    __tablename__ = "creators"

    id                    = Column(String, primary_key=True)   # UUID
    email                 = Column(String, unique=True, nullable=False, index=True)
    name                  = Column(String, nullable=False)
    tier                  = Column(String, default="free")      # free/creator/pro
    is_active             = Column(Boolean, default=True)
    stripe_customer_id    = Column(String, nullable=True, unique=True)
    stripe_subscription_id = Column(String, nullable=True)
    trial_ends_at         = Column(Float, nullable=True)        # unix timestamp

    # Brand kit stored as JSON
    brand_kit             = Column(JSON, nullable=True)

    # Timestamps
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    sessions              = relationship("StreamSession", back_populates="creator")
    viewers               = relationship("ViewerMemory", back_populates="creator")

    def __repr__(self):
        return f"<Creator {self.email} [{self.tier}]>"

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "email":        self.email,
            "name":         self.name,
            "tier":         self.tier,
            "is_active":    self.is_active,
            "brand_kit":    self.brand_kit,
            "created_at":   self.created_at.isoformat() if self.created_at else None,
        }


class StreamSession(Base):
    """A single live stream session."""
    __tablename__ = "stream_sessions"

    id                  = Column(String, primary_key=True)
    creator_id          = Column(String, ForeignKey("creators.id"), nullable=False, index=True)
    platform            = Column(String, nullable=False)        # tiktok/twitch/youtube/all
    started_at          = Column(DateTime, default=datetime.utcnow)
    ended_at            = Column(DateTime, nullable=True)
    duration_s          = Column(Float, nullable=True)

    # Metrics
    comments_processed  = Column(Integer, default=0)
    responses_spoken    = Column(Integer, default=0)
    unique_viewers      = Column(Integer, default=0)
    gift_revenue        = Column(Float, default=0.0)
    new_followers       = Column(Integer, default=0)
    new_subscribers     = Column(Integer, default=0)

    # Relationship
    creator             = relationship("Creator", back_populates="sessions")

    def __repr__(self):
        return f"<Session {self.id} [{self.platform}]>"

    def to_dict(self) -> dict:
        return {
            "id":                 self.id,
            "creator_id":        self.creator_id,
            "platform":          self.platform,
            "started_at":        self.started_at.isoformat() if self.started_at else None,
            "ended_at":          self.ended_at.isoformat() if self.ended_at else None,
            "duration_s":        self.duration_s,
            "comments_processed": self.comments_processed,
            "responses_spoken":  self.responses_spoken,
            "unique_viewers":    self.unique_viewers,
            "gift_revenue":      self.gift_revenue,
        }


class ViewerMemory(Base):
    """
    Persistent viewer memory — survives across sessions.
    The agent uses this to recognize repeat viewers and personalize responses.
    """
    __tablename__ = "viewer_memory"
    __table_args__ = (
        UniqueConstraint("creator_id", "platform", "username", name="uq_viewer"),
        Index("ix_viewer_lookup", "creator_id", "platform", "username"),
    )

    id              = Column(Integer, primary_key=True, autoincrement=True)
    creator_id      = Column(String, ForeignKey("creators.id"), nullable=False)
    platform        = Column(String, nullable=False)     # tiktok/twitch/youtube
    username        = Column(String, nullable=False)

    # Stats
    visit_count     = Column(Integer, default=1)
    first_seen      = Column(Float, default=time.time)
    last_seen       = Column(Float, default=time.time)
    gifted_total    = Column(Float, default=0.0)
    is_subscriber   = Column(Boolean, default=False)

    # Notable facts (list stored as JSON)
    notable_facts   = Column(JSON, default=list)

    # Relationship
    creator         = relationship("Creator", back_populates="viewers")

    def __repr__(self):
        return f"<Viewer @{self.username} on {self.platform} [{self.visit_count} visits]>"

    def to_dict(self) -> dict:
        return {
            "username":      self.username,
            "platform":      self.platform,
            "visit_count":   self.visit_count,
            "gifted_total":  self.gifted_total,
            "is_subscriber": self.is_subscriber,
            "notable_facts": self.notable_facts or [],
            "last_seen":     self.last_seen,
        }


class CommentLog(Base):
    """
    Log of every comment processed.
    Used for analytics and improving the agent over time.
    """
    __tablename__ = "comment_log"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    session_id      = Column(String, ForeignKey("stream_sessions.id"), nullable=True, index=True)
    creator_id      = Column(String, nullable=False, index=True)
    platform        = Column(String, nullable=False)
    username        = Column(String, nullable=False)
    comment_text    = Column(Text, nullable=False)
    comment_type    = Column(String, nullable=False)   # question/gift/spam/etc
    gift_value      = Column(Float, default=0.0)
    response_text   = Column(Text, nullable=True)       # None if skipped
    response_tone   = Column(String, nullable=True)
    was_spoken      = Column(Boolean, default=False)
    timestamp       = Column(Float, default=time.time)

    def to_dict(self) -> dict:
        return {
            "platform":      self.platform,
            "username":      self.username,
            "comment":       self.comment_text,
            "type":          self.comment_type,
            "response":      self.response_text,
            "was_spoken":    self.was_spoken,
            "timestamp":     self.timestamp,
        }


class WaitlistEntry(Base):
    """
    Waitlist signups from the landing page.
    Synced to ConvertKit via webhook.
    """
    __tablename__ = "waitlist"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    email           = Column(String, unique=True, nullable=False, index=True)
    source          = Column(String, default="landing-page")
    signed_up_at    = Column(DateTime, default=datetime.utcnow)
    converted       = Column(Boolean, default=False)   # became a paying creator

    def to_dict(self) -> dict:
        return {
            "email":       self.email,
            "source":      self.source,
            "signed_up_at": self.signed_up_at.isoformat() if self.signed_up_at else None,
            "converted":   self.converted,
        }


# ── CREATE TABLES ─────────────────────────────────────────────────────────────

def init_db():
    """Create all tables. Call once on startup."""
    Base.metadata.create_all(bind=engine)
    print(f"[DB] Tables created — {DATABASE_URL.split('///')[0]}")


def get_db() -> Session:
    """FastAPI dependency — yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
