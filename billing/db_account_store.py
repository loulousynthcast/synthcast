"""
billing/db_account_store.py

PostgreSQL-backed account store for Synthcast creators.
Replaces InMemoryAccountStore with persistent database storage.
Accounts survive Railway redeployments.

Uses the same interface as InMemoryAccountStore so it's a drop-in replacement.
"""

import os
import time
from typing import Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

load_dotenv()

from database.models import Base, Creator
from billing.stripe_billing import CreatorAccount, CreatorTier

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///synthcast.db")

# Fix for Railway PostgreSQL URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=engine)


def _db_to_account(db_creator: Creator) -> CreatorAccount:
    """Convert database Creator model to CreatorAccount dataclass."""
    account = CreatorAccount(
        creator_id=db_creator.id,
        email=db_creator.email,
        stripe_customer_id=db_creator.stripe_customer_id,
        stripe_subscription_id=db_creator.stripe_subscription_id,
        tier=CreatorTier(db_creator.tier),
        is_active=db_creator.is_active,
        trial_ends_at=db_creator.trial_ends_at,
        created_at=db_creator.created_at.timestamp() if db_creator.created_at else time.time(),
        elevenlabs_api_key=db_creator.elevenlabs_api_key,
        elevenlabs_voice_id=db_creator.elevenlabs_voice_id,
        heygen_api_key=db_creator.heygen_api_key,
        heygen_avatar_id=db_creator.heygen_avatar_id,
        openai_api_key=db_creator.openai_api_key,
    )
    account.name = db_creator.name
    return account


def _account_to_db(account: CreatorAccount, db_creator: Creator) -> Creator:
    """Update database Creator model from CreatorAccount dataclass."""
    db_creator.email = account.email
    db_creator.tier = account.tier.value
    db_creator.is_active = account.is_active
    db_creator.stripe_customer_id = account.stripe_customer_id
    db_creator.stripe_subscription_id = account.stripe_subscription_id
    db_creator.trial_ends_at = account.trial_ends_at
    db_creator.elevenlabs_api_key = account.elevenlabs_api_key
    db_creator.elevenlabs_voice_id = account.elevenlabs_voice_id
    db_creator.heygen_api_key = account.heygen_api_key
    db_creator.heygen_avatar_id = account.heygen_avatar_id
    db_creator.openai_api_key = account.openai_api_key
    if hasattr(account, 'name'):
        db_creator.name = account.name
    return db_creator


class PostgresAccountStore:
    """
    PostgreSQL-backed creator account store.
    Drop-in replacement for InMemoryAccountStore.
    Accounts persist across Railway redeployments.
    """

    def __init__(self):
        init_db()
        print("[DB] PostgreSQL account store initialized")

    def _get_session(self) -> Session:
        return SessionLocal()

    def get(self, creator_id: str) -> Optional[CreatorAccount]:
        with self._get_session() as db:
            creator = db.query(Creator).filter(Creator.id == creator_id).first()
            if not creator:
                return None
            return _db_to_account(creator)

    def get_by_email(self, email: str) -> Optional[CreatorAccount]:
        with self._get_session() as db:
            creator = db.query(Creator).filter(Creator.email == email).first()
            if not creator:
                return None
            return _db_to_account(creator)

    def create(self, creator_id: str, email: str, name: str) -> CreatorAccount:
        with self._get_session() as db:
            # Check if already exists
            existing = db.query(Creator).filter(Creator.id == creator_id).first()
            if existing:
                return _db_to_account(existing)

            # Create Stripe customer
            stripe_customer_id = None
            try:
                import stripe
                stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
                if stripe.api_key:
                    customer = stripe.Customer.create(email=email, name=name)
                    stripe_customer_id = customer.id
            except Exception as e:
                print(f"[DB] Stripe customer creation failed: {e}")

            creator = Creator(
                id=creator_id,
                email=email,
                name=name,
                tier="free",
                is_active=True,
                stripe_customer_id=stripe_customer_id,
            )
            db.add(creator)
            db.commit()
            db.refresh(creator)
            print(f"[DB] Created creator: {creator_id}")
            return _db_to_account(creator)

    def save(self, account: CreatorAccount) -> None:
        with self._get_session() as db:
            creator = db.query(Creator).filter(Creator.id == account.creator_id).first()
            if not creator:
                creator = Creator(id=account.creator_id)
                db.add(creator)
            _account_to_db(account, creator)
            db.commit()
            print(f"[DB] Saved creator: {account.creator_id}")

    def delete(self, creator_id: str) -> bool:
        with self._get_session() as db:
            creator = db.query(Creator).filter(Creator.id == creator_id).first()
            if not creator:
                return False
            db.delete(creator)
            db.commit()
            return True

    def stats(self) -> dict:
        with self._get_session() as db:
            total = db.query(Creator).count()
            by_tier = {}
            for tier in CreatorTier:
                by_tier[tier.value] = db.query(Creator).filter(Creator.tier == tier.value).count()
            active = db.query(Creator).filter(Creator.is_active == True).count()
            return {
                "total": total,
                **by_tier,
                "active": active,
                "mrr": (
                    by_tier.get("creator", 0) * 29 +
                    by_tier.get("pro", 0) * 79
                ),
                "storage": "postgresql" if "postgresql" in DATABASE_URL else "sqlite",
            }

    def list_all(self) -> list[CreatorAccount]:
        with self._get_session() as db:
            creators = db.query(Creator).all()
            return [_db_to_account(c) for c in creators]
