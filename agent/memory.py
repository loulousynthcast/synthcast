"""
synthcast/agent/memory.py

Viewer memory store.
Tracks repeat viewers, gift totals, subscriber status,
and notable facts across live sessions.

Phase 1: In-memory (dict). Fast, no dependencies.
Phase 2: Swap to Redis for persistence across restarts.
Phase 3: Swap to Pinecone for semantic similarity search.
"""

import time
import json
from typing import Optional
from dataclasses import dataclass, field, asdict

from .response_engine import ViewerProfile, IncomingComment


# ── IN-MEMORY STORE (Phase 1 MVP) ────────────────────────────────────────────

class InMemoryViewerStore:
    """
    Simple dict-based memory. Lives for the duration of one stream session.
    Good enough for MVP. Swap to RedisViewerStore when you need persistence.
    """

    def __init__(self):
        # key: "platform:username"  value: ViewerProfile
        self._store: dict[str, ViewerProfile] = {}

    def _key(self, platform: str, username: str) -> str:
        return f"{platform.lower()}:{username.lower()}"

    def get_viewer(self, platform: str, username: str) -> Optional[ViewerProfile]:
        return self._store.get(self._key(platform, username))

    def update_viewer(
        self,
        platform: str,
        username: str,
        comment: IncomingComment,
    ) -> ViewerProfile:
        key = self._key(platform, username)
        viewer = self._store.get(key)

        if viewer is None:
            viewer = ViewerProfile(
                username=username,
                visit_count=1,
                first_seen=comment.timestamp,
                last_seen=comment.timestamp,
                is_subscriber=comment.is_subscriber,
            )
        else:
            viewer.visit_count += 1
            viewer.last_seen = comment.timestamp
            if comment.is_subscriber:
                viewer.is_subscriber = True

        if comment.gift_value > 0:
            viewer.gifted_total += comment.gift_value

        self._store[key] = viewer
        return viewer

    def add_fact(self, platform: str, username: str, fact: str):
        """Add a notable fact about a viewer (called manually or by NLP)."""
        key = self._key(platform, username)
        if key in self._store:
            if fact not in self._store[key].notable_facts:
                self._store[key].notable_facts.append(fact)

    def get_stats(self) -> dict:
        """Session stats for the analytics dashboard."""
        total    = len(self._store)
        repeats  = sum(1 for v in self._store.values() if v.visit_count > 1)
        subs     = sum(1 for v in self._store.values() if v.is_subscriber)
        gifters  = sum(1 for v in self._store.values() if v.gifted_total > 0)
        gift_rev = sum(v.gifted_total for v in self._store.values())
        return {
            "total_unique_viewers": total,
            "repeat_viewers": repeats,
            "subscribers": subs,
            "gifters": gifters,
            "total_gift_revenue": round(gift_rev, 2),
        }

    def export(self) -> list[dict]:
        """Export all viewer data as JSON-serializable list."""
        return [asdict(v) for v in self._store.values()]


# ── REDIS STORE (Phase 2 — persistent across restarts) ───────────────────────

class RedisViewerStore:
    """
    Redis-backed viewer store. Persists across stream sessions.
    Viewer data survives restarts, server crashes, etc.

    REQUIRES:
      pip install redis
      REDIS_URL env var (e.g. redis://localhost:6379)

    To swap in: just replace InMemoryViewerStore with RedisViewerStore
    in your app config. Same interface, no other changes needed.
    """

    TTL_SECONDS = 60 * 60 * 24 * 30   # 30 days

    def __init__(self, redis_url: str = "redis://localhost:6379", prefix: str = "sc:viewer:"):
        try:
            import redis as redis_lib
        except ImportError:
            raise ImportError("Run: pip install redis")
        self.r = redis_lib.from_url(redis_url, decode_responses=True)
        self.prefix = prefix

    def _key(self, platform: str, username: str) -> str:
        return f"{self.prefix}{platform.lower()}:{username.lower()}"

    def get_viewer(self, platform: str, username: str) -> Optional[ViewerProfile]:
        raw = self.r.get(self._key(platform, username))
        if not raw:
            return None
        data = json.loads(raw)
        return ViewerProfile(**data)

    def update_viewer(
        self,
        platform: str,
        username: str,
        comment: IncomingComment,
    ) -> ViewerProfile:
        key = self._key(platform, username)
        raw = self.r.get(key)

        if raw is None:
            viewer = ViewerProfile(
                username=username,
                visit_count=1,
                first_seen=comment.timestamp,
                last_seen=comment.timestamp,
                is_subscriber=comment.is_subscriber,
            )
        else:
            data = json.loads(raw)
            viewer = ViewerProfile(**data)
            viewer.visit_count += 1
            viewer.last_seen = comment.timestamp
            if comment.is_subscriber:
                viewer.is_subscriber = True

        if comment.gift_value > 0:
            viewer.gifted_total += comment.gift_value

        self.r.setex(key, self.TTL_SECONDS, json.dumps(asdict(viewer)))
        return viewer

    def add_fact(self, platform: str, username: str, fact: str):
        key = self._key(platform, username)
        raw = self.r.get(key)
        if raw:
            data = json.loads(raw)
            viewer = ViewerProfile(**data)
            if fact not in viewer.notable_facts:
                viewer.notable_facts.append(fact)
                self.r.setex(key, self.TTL_SECONDS, json.dumps(asdict(viewer)))

    def get_stats(self) -> dict:
        keys = self.r.keys(f"{self.prefix}*")
        viewers = [ViewerProfile(**json.loads(self.r.get(k))) for k in keys if self.r.get(k)]
        return {
            "total_unique_viewers": len(viewers),
            "repeat_viewers": sum(1 for v in viewers if v.visit_count > 1),
            "subscribers": sum(1 for v in viewers if v.is_subscriber),
            "gifters": sum(1 for v in viewers if v.gifted_total > 0),
            "total_gift_revenue": round(sum(v.gifted_total for v in viewers), 2),
        }


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_memory_store(backend: str = "memory", **kwargs):
    """
    Factory. Switch backends in config without touching engine code.

        store = get_memory_store("memory")          # MVP
        store = get_memory_store("redis", redis_url="redis://localhost:6379")
    """
    if backend == "memory":
        return InMemoryViewerStore()
    elif backend == "redis":
        return RedisViewerStore(**kwargs)
    else:
        raise ValueError(f"Unknown backend '{backend}'. Choose: memory, redis")
