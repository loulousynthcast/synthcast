"""
synthcast/stream/tiktok_listener.py
Fixed for TikTokLive v6.6.5 - no Euler key parameter
"""

import os
import asyncio
import random
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class TikTokListener:

    def __init__(self, unique_id, agent_api_url="http://localhost:8000", simulation_mode=False):
        self.unique_id = unique_id.lstrip("@")
        self.agent_url = agent_api_url.rstrip("/")
        self.simulation_mode = simulation_mode
        self._forwarded = 0
        self._errors = 0
        self._running = False
        self._http = None
        if simulation_mode:
            print("[TikTok] SIMULATION MODE")
        else:
            print(f"[TikTok] Ready for @{self.unique_id}")

    async def start(self):
        self._running = True
        async with httpx.AsyncClient(timeout=10.0) as http:
            self._http = http
            if self.simulation_mode:
                await self._simulate()
            else:
                await self._connect_live()

    def stop(self):
        self._running = False
        print(f"[TikTok] Stopped. Forwarded {self._forwarded} events.")

    async def _connect_live(self):
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import (
                ConnectEvent, DisconnectEvent, LiveEndEvent,
                CommentEvent, GiftEvent, FollowEvent,
                JoinEvent, SubscribeEvent,
            )
        except ImportError:
            print("Run: pip install TikTokLive")
            return

        client = TikTokLiveClient(unique_id=f"@{self.unique_id}")

        @client.on(ConnectEvent)
        async def on_connect(e):
            print(f"[TikTok] Connected to @{self.unique_id} live stream!")

        @client.on(DisconnectEvent)
        async def on_disconnect(e):
            print("[TikTok] Disconnected")

        @client.on(LiveEndEvent)
        async def on_end(e):
            print("[TikTok] Stream ended")
            self._running = False

        @client.on(CommentEvent)
        async def on_comment(e):
            await self._forward({
                "platform": "tiktok",
                "username": e.user.nickname or e.user.unique_id,
                "text": e.comment,
                "gift_value": 0.0,
                "is_new_follower": False,
                "is_subscriber": getattr(e.user, "is_subscriber", False),
            })

        @client.on(GiftEvent)
        async def on_gift(e):
            if getattr(e.gift, "streakable", False) and not getattr(e.gift, "streak_ended", True):
                return
            diamonds = getattr(e.gift, "diamond_count", 0) or 0
            count = getattr(e.gift, "repeat_count", 1) or 1
            name = getattr(e.gift, "name", "a gift") or "a gift"
            value = round(diamonds * 0.0105, 2) * count
            await self._forward({
                "platform": "tiktok",
                "username": e.user.nickname or e.user.unique_id,
                "text": f"sent {count}x {name}",
                "gift_value": value,
                "is_new_follower": False,
                "is_subscriber": getattr(e.user, "is_subscriber", False),
            })

        @client.on(FollowEvent)
        async def on_follow(e):
            await self._forward({
                "platform": "tiktok",
                "username": e.user.nickname or e.user.unique_id,
                "text": "just followed!",
                "gift_value": 0.0,
                "is_new_follower": True,
                "is_subscriber": False,
            })

        @client.on(JoinEvent)
        async def on_join(e):
            if random.random() > 0.2:
                return
            await self._forward({
                "platform": "tiktok",
                "username": e.user.nickname or e.user.unique_id,
                "text": "joined the stream",
                "gift_value": 0.0,
                "is_new_follower": False,
                "is_subscriber": False,
            })

        @client.on(SubscribeEvent)
        async def on_subscribe(e):
            await self._forward({
                "platform": "tiktok",
                "username": e.user.nickname or e.user.unique_id,
                "text": "just subscribed!",
                "gift_value": 0.0,
                "is_new_follower": False,
                "is_subscriber": True,
            })

        print(f"[TikTok] Connecting to @{self.unique_id}...")
        print(f"[TikTok] Make sure you are LIVE on TikTok right now")

        try:
            await client.start()
        except Exception as e:
            msg = str(e).lower()
            if "not live" in msg or "offline" in msg:
                print(f"\n@{self.unique_id} is not live right now.")
                print("Go live on TikTok then run this again.")
            elif "rate" in msg or "captcha" in msg:
                print("\nRate limited. Wait a few minutes and try again.")
            else:
                print(f"\nConnection error: {e}")

    async def _simulate(self):
        EVENTS = [
            {"username": "creator_fan_88",  "text": "What camera are you using?",        "gift_value": 0,    "is_new_follower": False},
            {"username": "nightowl_live",   "text": "Love this stream!",                  "gift_value": 0,    "is_new_follower": False},
            {"username": "newviewer2025",   "text": "just followed!",                     "gift_value": 0,    "is_new_follower": True},
            {"username": "techguru_99",     "text": "How long have you been doing this?", "gift_value": 0,    "is_new_follower": False},
            {"username": "loyal_fan_01",    "text": "you are the GOAT",                   "gift_value": 0,    "is_new_follower": False},
            {"username": "gifter_king",     "text": "sent 1x Rose",                       "gift_value": 0.05, "is_new_follower": False},
            {"username": "mega_gifter",     "text": "sent 5x Lion",                       "gift_value": 25.0, "is_new_follower": False},
            {"username": "spam_bot",        "text": "follow me follow back!!!",            "gift_value": 0,    "is_new_follower": False},
            {"username": "curious_one",     "text": "what editing software do you use?",  "gift_value": 0,    "is_new_follower": False},
            {"username": "subscriber_vip",  "text": "just subscribed!",                   "gift_value": 0,    "is_new_follower": False, "is_subscriber": True},
        ]
        print("[TikTok] Simulation running — Ctrl+C to stop")
        while self._running:
            event = random.choice(EVENTS)
            await self._forward({
                "platform": "tiktok",
                "username": event["username"],
                "text": event["text"],
                "gift_value": event.get("gift_value", 0.0),
                "is_new_follower": event.get("is_new_follower", False),
                "is_subscriber": event.get("is_subscriber", False),
            })
            await asyncio.sleep(random.uniform(1.5, 4.0))

    async def _forward(self, payload):
        if not self._http:
            return
        try:
            resp = await self._http.post(f"{self.agent_url}/comment/process", json=payload)
            resp.raise_for_status()
            result = resp.json()
            self._forwarded += 1
            u = payload["username"]
            t = payload["text"][:50]
            if result:
                print(f"  @{u}: {t}")
                print(f"  -> [{result.get('tone','?')}] {result['text'][:70]}")
            else:
                print(f"  [SKIP] @{u}: {t}")
        except httpx.ConnectError:
            self._errors += 1
            if self._errors <= 2:
                print(f"[TikTok] Cannot reach {self.agent_url}")
        except httpx.HTTPStatusError as e:
            self._errors += 1
            if e.response.status_code == 400:
                print(f"  [NO SESSION] Start a session first")
        except Exception as e:
            self._errors += 1
            if self._errors <= 5:
                print(f"  [ERROR] {e}")

    def stats(self):
        return {"unique_id": self.unique_id, "forwarded": self._forwarded, "errors": self._errors}


async def main():
    import json
    unique_id = os.getenv("TIKTOK_UNIQUE_ID", "").lstrip("@")
    api_url = os.getenv("SYNTHCAST_API_URL", "http://localhost:8000")
    sim_mode = not unique_id

    if not unique_id:
        print("TIKTOK_UNIQUE_ID not set — running simulation")
        unique_id = "simulation"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            await client.post(f"{api_url}/session/stop")
        except Exception:
            pass
        try:
            resp = await client.post(f"{api_url}/session/start", json={
                "brand_kit": {
                    "creator_name": os.getenv("CREATOR_NAME", "Creator"),
                    "avatar_name": os.getenv("AVATAR_NAME", "AI Avatar"),
                    "personality": os.getenv("CREATOR_PERSONALITY", "warm, confident, genuine"),
                    "banned_topics": ["politics", "religion"],
                    "banned_words": [],
                    "cta_scripts": ["Follow for more!", "Subscribe!"],
                },
                "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
                "memory_backend": "memory",
            })
            resp.raise_for_status()
            s = resp.json()
            print(f"Session: {s['session_id']} | Avatar: {s['avatar']}")
            print("-" * 60)
        except httpx.ConnectError:
            print(f"Cannot reach {api_url}")
            print("Start the API: python -m uvicorn api.main:app --port 8000")
            return
        except Exception as e:
            print(f"Session start failed: {e}")
            return

    listener = TikTokListener(unique_id=unique_id, agent_api_url=api_url, simulation_mode=sim_mode)
    try:
        await listener.start()
    except KeyboardInterrupt:
        listener.stop()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{api_url}/session/stop")
                print("\nFinal stats:", json.dumps(resp.json(), indent=2))
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
