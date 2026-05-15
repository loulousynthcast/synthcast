"""
synthcast/stream/orchestrator.py

Full pipeline orchestrator.
Runs TikTok + Twitch + YouTube + Instagram simultaneously.
One agent engine handles all platforms at once.

Usage:
    python -m stream.orchestrator

Set active platforms in .env:
    ACTIVE_PLATFORMS=tiktok,twitch,youtube,instagram
"""

import os
import asyncio
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stream.tts import get_tts_engine
from stream.tiktok_listener import TikTokListener
from stream.platform_listeners import MultiPlatformManager


class StreamOrchestrator:
    def __init__(
        self,
        agent_api_url: str = "http://localhost:8000",
        creator_name: str  = "Creator",
        avatar_name: str   = "AI Avatar",
        personality: str   = "warm, confident, genuine",
        banned_topics: Optional[list] = None,
        llm_provider: str  = "openai",
        active_platforms: Optional[list] = None,
        simulation_mode: bool = True,
    ):
        self.api_url          = agent_api_url.rstrip("/")
        self.creator_name     = creator_name
        self.avatar_name      = avatar_name
        self.personality      = personality
        self.banned_topics    = banned_topics or ["politics", "religion"]
        self.llm_provider     = llm_provider
        self.active_platforms = active_platforms or ["tiktok", "twitch", "youtube", "instagram"]
        self.simulation_mode  = simulation_mode
        self.tts = get_tts_engine()
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._session_id: Optional[str] = None

    async def run(self):
        async with httpx.AsyncClient(timeout=15.0) as client:
            await self._start_session(client)
            self._running = True
            tasks = []

            # TikTok
            if "tiktok" in self.active_platforms:
                unique_id = os.getenv("TIKTOK_UNIQUE_ID", "").lstrip("@")
                tiktok = TikTokListener(
                    unique_id=unique_id or "simulation",
                    agent_api_url=self.api_url,
                    simulation_mode=self.simulation_mode or not unique_id,
                )
                tasks.append(tiktok.start())

            # Other platforms
            other = [p for p in self.active_platforms if p != "tiktok"]
            if other:
                manager = MultiPlatformManager(
                    agent_api_url=self.api_url,
                    platforms=other,
                    simulation_mode=self.simulation_mode,
                )
                tasks.append(manager.run())

            tasks.append(self._tts_player())
            tasks.append(self._status_printer())

            try:
                await asyncio.gather(*tasks)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\n[Orchestrator] Shutting down...")
            finally:
                self._running = False
                await self._stop_session(client)

    async def _tts_player(self):
        print("[TTS] Player ready")
        while self._running:
            try:
                response = await asyncio.wait_for(self._response_queue.get(), timeout=1.0)
                text = response.get("text", "")
                if text:
                    await self.tts.speak(text, play=True)
                    self._response_queue.task_done()
            except asyncio.TimeoutError:
                continue

    async def _status_printer(self):
        async with httpx.AsyncClient() as client:
            while self._running:
                await asyncio.sleep(60)
                try:
                    resp = await client.get(f"{self.api_url}/session/status")
                    s = resp.json()
                    mins = round((s.get("session_duration_s") or 0) / 60, 1)
                    mem  = s.get("memory_stats") or {}
                    print(
                        f"\n[STATUS] {mins}min | "
                        f"processed={s['comments_processed']} | "
                        f"spoken={s['responses_spoken']} | "
                        f"viewers={mem.get('total_unique_viewers',0)} | "
                        f"revenue=${mem.get('total_gift_revenue',0):.2f}"
                    )
                except Exception:
                    pass

    async def _start_session(self, client: httpx.AsyncClient):
        print(f"[Orchestrator] Platforms: {', '.join(self.active_platforms)}")
        try:
            await client.post(f"{self.api_url}/session/stop")
        except Exception:
            pass

        resp = await client.post(f"{self.api_url}/session/start", json={
            "brand_kit": {
                "creator_name":  self.creator_name,
                "avatar_name":   self.avatar_name,
                "personality":   self.personality,
                "banned_topics": self.banned_topics,
                "banned_words":  [],
                "cta_scripts":   ["Follow for more!", "Subscribe to support the stream!"],
            },
            "llm_provider":   self.llm_provider,
            "memory_backend": "memory",
        })

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to start session: {resp.text}")

        data = resp.json()
        self._session_id = data["session_id"]
        print(f"[Orchestrator] Session: {self._session_id} | Avatar: {data['avatar']}")
        print("─" * 60)

    async def _stop_session(self, client: httpx.AsyncClient):
        try:
            resp = await client.post(f"{self.api_url}/session/stop")
            data = resp.json()
            print(f"\n[Orchestrator] Done — {data.get('duration_min',0)} min | "
                  f"{data.get('comments_processed',0)} comments | "
                  f"{data.get('responses_spoken',0)} spoken")
        except Exception:
            pass


async def main():
    platforms_env = os.getenv("ACTIVE_PLATFORMS", "tiktok,twitch,youtube,instagram")
    active = [p.strip() for p in platforms_env.split(",")]
    sim_mode = (
        os.getenv("SIMULATION_MODE", "").lower() == "true"
        or not os.getenv("TIKTOK_UNIQUE_ID")
    )
    orch = StreamOrchestrator(
        agent_api_url    = os.getenv("SYNTHCAST_API_URL", "http://localhost:8000"),
        creator_name     = os.getenv("CREATOR_NAME",      "Creator"),
        avatar_name      = os.getenv("AVATAR_NAME",       "AI Avatar"),
        personality      = os.getenv("CREATOR_PERSONALITY","warm, confident, genuine"),
        llm_provider     = os.getenv("LLM_PROVIDER",      "openai"),
        active_platforms = active,
        simulation_mode  = sim_mode,
    )
    await orch.run()


if __name__ == "__main__":
    asyncio.run(main())
