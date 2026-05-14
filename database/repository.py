"""
synthcast/database/repository.py

Data access layer — clean methods for reading and writing
every model. The API and agent call these instead of
writing raw SQL queries.

All methods are synchronous — wrap in asyncio.run_in_executor
for async FastAPI endpoints.
"""

import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from .models import (
    Creator, StreamSession, ViewerMemory,
    CommentLog, WaitlistEntry
)


# ── CREATOR REPOSITORY ────────────────────────────────────────────────────────

class CreatorRepo:

    def __init__(self, db: Session):
        self.db = db

    def create(self, email: str, name: str, tier: str = "free") -> Creator:
        creator = Creator(
            id=str(uuid.uuid4()),
            email=email.lower().strip(),
            name=name,
            tier=tier,
        )
        self.db.add(creator)
        self.db.commit()
        self.db.refresh(creator)
        return creator

    def get(self, creator_id: str) -> Optional[Creator]:
        return self.db.query(Creator).filter(Creator.id == creator_id).first()

    def get_by_email(self, email: str) -> Optional[Creator]:
        return self.db.query(Creator).filter(
            Creator.email == email.lower().strip()
        ).first()

    def get_by_stripe_customer(self, stripe_customer_id: str) -> Optional[Creator]:
        return self.db.query(Creator).filter(
            Creator.stripe_customer_id == stripe_customer_id
        ).first()

    def update_tier(self, creator_id: str, tier: str) -> Optional[Creator]:
        creator = self.get(creator_id)
        if creator:
            creator.tier = tier
            creator.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(creator)
        return creator

    def update_stripe(
        self,
        creator_id: str,
        customer_id: str,
        subscription_id: Optional[str] = None,
    ) -> Optional[Creator]:
        creator = self.get(creator_id)
        if creator:
            creator.stripe_customer_id     = customer_id
            creator.stripe_subscription_id = subscription_id
            creator.updated_at             = datetime.utcnow()
            self.db.commit()
            self.db.refresh(creator)
        return creator

    def save_brand_kit(self, creator_id: str, brand_kit: dict) -> Optional[Creator]:
        creator = self.get(creator_id)
        if creator:
            creator.brand_kit  = brand_kit
            creator.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(creator)
        return creator

    def set_active(self, creator_id: str, active: bool) -> Optional[Creator]:
        creator = self.get(creator_id)
        if creator:
            creator.is_active  = active
            creator.updated_at = datetime.utcnow()
            self.db.commit()
        return creator

    def all(self) -> list[Creator]:
        return self.db.query(Creator).all()

    def stats(self) -> dict:
        from .models import Creator as C
        total   = self.db.query(func.count(C.id)).scalar()
        by_tier = (
            self.db.query(C.tier, func.count(C.id))
            .group_by(C.tier)
            .all()
        )
        tier_map = {tier: count for tier, count in by_tier}
        mrr = (
            tier_map.get("creator", 0) * 29.0 +
            tier_map.get("pro", 0) * 79.0
        )
        return {
            "total":   total,
            "free":    tier_map.get("free", 0),
            "creator": tier_map.get("creator", 0),
            "pro":     tier_map.get("pro", 0),
            "mrr":     mrr,
        }


# ── SESSION REPOSITORY ────────────────────────────────────────────────────────

class SessionRepo:

    def __init__(self, db: Session):
        self.db = db

    def create(self, creator_id: str, platform: str = "all") -> StreamSession:
        session = StreamSession(
            id=f"session_{int(time.time())}_{creator_id[:8]}",
            creator_id=creator_id,
            platform=platform,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get(self, session_id: str) -> Optional[StreamSession]:
        return self.db.query(StreamSession).filter(
            StreamSession.id == session_id
        ).first()

    def end_session(
        self,
        session_id: str,
        stats: dict,
    ) -> Optional[StreamSession]:
        session = self.get(session_id)
        if session:
            session.ended_at           = datetime.utcnow()
            session.duration_s         = stats.get("duration_s", 0)
            session.comments_processed = stats.get("comments_processed", 0)
            session.responses_spoken   = stats.get("responses_spoken", 0)
            session.unique_viewers     = stats.get("unique_viewers", 0)
            session.gift_revenue       = stats.get("gift_revenue", 0.0)
            session.new_followers      = stats.get("new_followers", 0)
            session.new_subscribers    = stats.get("new_subscribers", 0)
            self.db.commit()
            self.db.refresh(session)
        return session

    def recent(self, creator_id: str, limit: int = 10) -> list[StreamSession]:
        return (
            self.db.query(StreamSession)
            .filter(StreamSession.creator_id == creator_id)
            .order_by(desc(StreamSession.started_at))
            .limit(limit)
            .all()
        )

    def lifetime_stats(self, creator_id: str) -> dict:
        sessions = (
            self.db.query(StreamSession)
            .filter(
                StreamSession.creator_id == creator_id,
                StreamSession.ended_at != None,
            )
            .all()
        )
        return {
            "total_sessions":    len(sessions),
            "total_hours":       round(sum(s.duration_s or 0 for s in sessions) / 3600, 1),
            "total_comments":    sum(s.comments_processed or 0 for s in sessions),
            "total_responses":   sum(s.responses_spoken or 0 for s in sessions),
            "total_revenue":     round(sum(s.gift_revenue or 0 for s in sessions), 2),
            "total_followers":   sum(s.new_followers or 0 for s in sessions),
        }


# ── VIEWER MEMORY REPOSITORY ──────────────────────────────────────────────────

class ViewerRepo:

    def __init__(self, db: Session):
        self.db = db

    def get(
        self,
        creator_id: str,
        platform: str,
        username: str,
    ) -> Optional[ViewerMemory]:
        return (
            self.db.query(ViewerMemory)
            .filter(
                ViewerMemory.creator_id == creator_id,
                ViewerMemory.platform   == platform.lower(),
                ViewerMemory.username   == username.lower(),
            )
            .first()
        )

    def upsert(
        self,
        creator_id: str,
        platform: str,
        username: str,
        gift_value: float = 0.0,
        is_subscriber: bool = False,
    ) -> ViewerMemory:
        viewer = self.get(creator_id, platform, username)
        now = time.time()

        if viewer is None:
            viewer = ViewerMemory(
                creator_id    = creator_id,
                platform      = platform.lower(),
                username      = username.lower(),
                visit_count   = 1,
                first_seen    = now,
                last_seen     = now,
                gifted_total  = gift_value,
                is_subscriber = is_subscriber,
                notable_facts = [],
            )
            self.db.add(viewer)
        else:
            viewer.visit_count  += 1
            viewer.last_seen     = now
            viewer.gifted_total += gift_value
            if is_subscriber:
                viewer.is_subscriber = True

        self.db.commit()
        self.db.refresh(viewer)
        return viewer

    def add_fact(
        self,
        creator_id: str,
        platform: str,
        username: str,
        fact: str,
    ) -> Optional[ViewerMemory]:
        viewer = self.get(creator_id, platform, username)
        if viewer:
            facts = list(viewer.notable_facts or [])
            if fact not in facts:
                facts.append(fact)
                viewer.notable_facts = facts
                self.db.commit()
                self.db.refresh(viewer)
        return viewer

    def top_viewers(self, creator_id: str, limit: int = 20) -> list[ViewerMemory]:
        return (
            self.db.query(ViewerMemory)
            .filter(ViewerMemory.creator_id == creator_id)
            .order_by(desc(ViewerMemory.visit_count))
            .limit(limit)
            .all()
        )

    def top_gifters(self, creator_id: str, limit: int = 10) -> list[ViewerMemory]:
        return (
            self.db.query(ViewerMemory)
            .filter(
                ViewerMemory.creator_id == creator_id,
                ViewerMemory.gifted_total > 0,
            )
            .order_by(desc(ViewerMemory.gifted_total))
            .limit(limit)
            .all()
        )

    def stats(self, creator_id: str) -> dict:
        viewers = (
            self.db.query(ViewerMemory)
            .filter(ViewerMemory.creator_id == creator_id)
            .all()
        )
        return {
            "total_unique_viewers": len(viewers),
            "repeat_viewers":       sum(1 for v in viewers if v.visit_count > 1),
            "subscribers":          sum(1 for v in viewers if v.is_subscriber),
            "gifters":              sum(1 for v in viewers if v.gifted_total > 0),
            "total_gift_revenue":   round(sum(v.gifted_total for v in viewers), 2),
        }


# ── COMMENT LOG REPOSITORY ────────────────────────────────────────────────────

class CommentRepo:

    def __init__(self, db: Session):
        self.db = db

    def log(
        self,
        creator_id: str,
        platform: str,
        username: str,
        comment_text: str,
        comment_type: str,
        gift_value: float = 0.0,
        response_text: Optional[str] = None,
        response_tone: Optional[str] = None,
        was_spoken: bool = False,
        session_id: Optional[str] = None,
    ) -> CommentLog:
        entry = CommentLog(
            session_id    = session_id,
            creator_id    = creator_id,
            platform      = platform,
            username      = username,
            comment_text  = comment_text,
            comment_type  = comment_type,
            gift_value    = gift_value,
            response_text = response_text,
            response_tone = response_tone,
            was_spoken    = was_spoken,
            timestamp     = time.time(),
        )
        self.db.add(entry)
        self.db.commit()
        return entry

    def recent(
        self,
        creator_id: str,
        limit: int = 50,
        platform: Optional[str] = None,
    ) -> list[CommentLog]:
        q = (
            self.db.query(CommentLog)
            .filter(CommentLog.creator_id == creator_id)
        )
        if platform:
            q = q.filter(CommentLog.platform == platform)
        return q.order_by(desc(CommentLog.timestamp)).limit(limit).all()

    def type_breakdown(self, creator_id: str) -> dict:
        rows = (
            self.db.query(CommentLog.comment_type, func.count(CommentLog.id))
            .filter(CommentLog.creator_id == creator_id)
            .group_by(CommentLog.comment_type)
            .all()
        )
        return {t: c for t, c in rows}


# ── WAITLIST REPOSITORY ───────────────────────────────────────────────────────

class WaitlistRepo:

    def __init__(self, db: Session):
        self.db = db

    def add(self, email: str, source: str = "landing-page") -> WaitlistEntry:
        existing = self.db.query(WaitlistEntry).filter(
            WaitlistEntry.email == email.lower()
        ).first()
        if existing:
            return existing

        entry = WaitlistEntry(
            email=email.lower().strip(),
            source=source,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def count(self) -> int:
        return self.db.query(func.count(WaitlistEntry.id)).scalar()

    def all(self) -> list[WaitlistEntry]:
        return (
            self.db.query(WaitlistEntry)
            .order_by(desc(WaitlistEntry.signed_up_at))
            .all()
        )

    def mark_converted(self, email: str):
        entry = self.db.query(WaitlistEntry).filter(
            WaitlistEntry.email == email.lower()
        ).first()
        if entry:
            entry.converted = True
            self.db.commit()
