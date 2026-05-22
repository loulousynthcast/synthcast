"""
billing/db_auth_store.py
PostgreSQL-backed auth store for Synthcast.
Replaces the in-memory _users dict in auth_routes.py.
Accounts survive Railway redeploys.
"""

import os
import uuid
import time
from typing import Optional
from sqlalchemy import create_engine, Column, String, Float, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///synthcast_auth.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class AuthUser(Base):
    __tablename__ = "auth_users"
    creator_id          = Column(String, primary_key=True)
    email               = Column(String, unique=True, nullable=False, index=True)
    name                = Column(String, nullable=False)
    password_hash       = Column(String, nullable=False)
    tier                = Column(String, default="free")
    created_at          = Column(Float, default=time.time)
    google_id           = Column(String, nullable=True)
    bio                 = Column(Text, nullable=True)
    creator_handle      = Column(String, nullable=True)
    avatar_name         = Column(String, nullable=True)
    photo_url           = Column(String, nullable=True)
    elevenlabs_api_key  = Column(String, nullable=True)
    elevenlabs_voice_id = Column(String, nullable=True)
    openai_api_key      = Column(String, nullable=True)
    heygen_api_key      = Column(String, nullable=True)
    heygen_avatar_id    = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)
print("[AuthDB] PostgreSQL auth store initialized")


def _row_to_dict(user: AuthUser) -> dict:
    return {
        "creator_id":          user.creator_id,
        "email":               user.email,
        "name":                user.name,
        "password_hash":       user.password_hash,
        "tier":                user.tier,
        "created_at":          user.created_at,
        "google_id":           user.google_id,
        "bio":                 user.bio,
        "creator_handle":      user.creator_handle,
        "avatar_name":         user.avatar_name,
        "photo_url":           user.photo_url,
        "elevenlabs_api_key":  user.elevenlabs_api_key,
        "elevenlabs_voice_id": user.elevenlabs_voice_id,
        "openai_api_key":      user.openai_api_key,
        "heygen_api_key":      user.heygen_api_key,
        "heygen_avatar_id":    user.heygen_avatar_id,
    }


def get_user_by_email(email: str) -> Optional[dict]:
    with SessionLocal() as db:
        user = db.query(AuthUser).filter(AuthUser.email == email.lower()).first()
        return _row_to_dict(user) if user else None


def get_user_by_id(creator_id: str) -> Optional[dict]:
    with SessionLocal() as db:
        user = db.query(AuthUser).filter(AuthUser.creator_id == creator_id).first()
        return _row_to_dict(user) if user else None


def create_user(email: str, name: str, password_hash: str, tier: str = "free",
                google_id: str = None, creator_id: str = None) -> dict:
    if not creator_id:
        creator_id = email.split("@")[0].lower().replace(".", "_") + "_" + str(uuid.uuid4())[:6]

    with SessionLocal() as db:
        existing = db.query(AuthUser).filter(AuthUser.email == email.lower()).first()
        if existing:
            return _row_to_dict(existing)

        user = AuthUser(
            creator_id=creator_id,
            email=email.lower(),
            name=name,
            password_hash=password_hash,
            tier=tier,
            created_at=time.time(),
            google_id=google_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"[AuthDB] Created user: {email}")
        return _row_to_dict(user)


def update_user(creator_id: str, **kwargs) -> Optional[dict]:
    with SessionLocal() as db:
        user = db.query(AuthUser).filter(AuthUser.creator_id == creator_id).first()
        if not user:
            return None
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.commit()
        db.refresh(user)
        return _row_to_dict(user)


def list_all_users() -> list:
    with SessionLocal() as db:
        users = db.query(AuthUser).all()
        return [_row_to_dict(u) for u in users]
