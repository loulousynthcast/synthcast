"""
synthcast/stream/platform_listeners.py

Live stream listeners for Twitch, YouTube, and Instagram.
All share the same interface as TikTokListener — plug into
the same agent engine and orchestrator with zero changes.

Install:
    pip install twitchio google-auth google-auth-httplib2 google-api-python-client httpx

YOUR PART (per platform):
  Twitch:    Get client ID + OAuth token at dev.twitch.tv
  YouTube:   Get API key + OAuth at console.cloud.google.com
  Instagram: Uses simulation only (Meta restricts Live comment API to approved partners)
"""

import os
import asyncio
import random
import httpx
from typing import Optional
from abc import ABC, abstractmethod
from dotenv import load_dotenv

load_dotenv()


# ── BASE LISTENER ─────────────────────────────────────────────────────────────

class BasePlatformListener(ABC):
    """
    Shared interface for all platform listeners.
    Every listener connects to a live stream and forwards
    events to the Synthcast agent API.
    """

    def __init__(
        self,
        agent_api_url: str = "http://localhost:8000",
        simulation_mode: bool = False,
    ):
        self.agent_url = agent_api_url.rstrip("/")
        self.simulation_mode = simulation_mode
        self._forwarded = 0
        self._errors = 0
        self._running = False
        self._http: Optional[httpx.AsyncClient] = None

    @property
    @abstractmethod
    def platform_name(self) -> str:
        ...

    @abstractmethod
    async def _connect_live(self): ...

    @abstractmethod
    async def _simulate(self): ...

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
        print(f"[{self.platform_name}] Stopped. Forwarded {self._forwarded} events.")

    async def _forward(self, payload: dict):
        if not self._http:
            return
        try:
            resp = await self._http.post(
                f"{self.agent_url}/comment/process",
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()
            self._forwarded += 1

            u = payload["username"]
            t = payload["text"][:50]
            if result:
                tone = result.get("tone", "?")
                dur  = result.get("estimated_duration_s", 0)
                print(f"  [{self.platform_name}] @{u}: {t}")
                print(f"    → [{tone}] {result['text'][:70]}  (~{dur}s)")
            else:
                print(f"  [{self.platform_name}] [SKIP] @{u}: {t}")

        except httpx.ConnectError:
            self._errors += 1
            if self._errors <= 2:
                print(f"[{self.platform_name}] Cannot reach {self.agent_url}")
                print("  Start the API: python -m uvicorn api.main:app --port 8000")
        except httpx.HTTPStatusError as e:
            self._errors += 1
            if e.response.status_code == 400:
                print(f"  [{self.platform_name}] No active session — call /session/start")
        except Exception as e:
            self._errors += 1
            if self._errors <= 5:
                print(f"  [{self.platform_name}] Error: {e}")

    def stats(self) -> dict:
        return {
            "platform":   self.platform_name,
            "mode":       "simulation" if self.simulation_mode else "live",
            "forwarded":  self._forwarded,
            "errors":     self._errors,
            "running":    self._running,
        }


# ── TWITCH ────────────────────────────────────────────────────────────────────

class TwitchListener(BasePlatformListener):
    """
    Connects to Twitch IRC chat and forwards messages to the agent.

    Uses TwitchIO library — simplest Twitch chat integration available.
    No special permissions needed — just a bot account and OAuth token.

    YOUR PART:
      1. Go to dev.twitch.tv → Your Console → Register Application
      2. Create a bot Twitch account (or use your own)
      3. Get OAuth token: https://twitchapps.com/tmi/
      4. Add to .env:
           TWITCH_TOKEN=oauth:xxxxxxxx
           TWITCH_CHANNEL=yourchannelname
           TWITCH_BOT_NICK=your_bot_username
    """

    FAKE_EVENTS = [
        {"username": "twitch_fan_01",   "text": "What's your setup?",              "gift_value": 0},
        {"username": "cheer_master",    "text": "Cheer100 This stream is fire!",   "gift_value": 1.0},
        {"username": "new_follower_tw", "text": "Just followed! Love the content", "gift_value": 0, "is_new_follower": True},
        {"username": "twitch_sub_vip",  "text": "just subscribed!",                "gift_value": 0, "is_subscriber": True},
        {"username": "lurker_7749",     "text": "What game is this?",              "gift_value": 0},
        {"username": "hype_train_01",   "text": "POGGERS this is insane",          "gift_value": 0},
        {"username": "bits_donor",      "text": "Cheer500 Keep it up!",            "gift_value": 5.0},
        {"username": "spam_follow_bot", "text": "follow me follow back",            "gift_value": 0},
    ]

    def __init__(
        self,
        channel: Optional[str] = None,
        token: Optional[str] = None,
        bot_nick: Optional[str] = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.channel  = channel  or os.getenv("TWITCH_CHANNEL", "")
        self.token    = token    or os.getenv("TWITCH_TOKEN", "")
        self.bot_nick = bot_nick or os.getenv("TWITCH_BOT_NICK", "synthcast_bot")

        if not self.simulation_mode:
            print(f"[Twitch] Ready for #{self.channel}")

    @property
    def platform_name(self) -> str:
        return "Twitch"

    async def _connect_live(self):
        try:
            from twitchio.ext import commands as twitch_commands
            import twitchio
        except ImportError:
            print("\nTwitchIO not installed. Run: pip install twitchio")
            print("Falling back to simulation mode.")
            await self._simulate()
            return

        if not self.token or not self.channel:
            print("[Twitch] TWITCH_TOKEN or TWITCH_CHANNEL not set in .env")
            print("Falling back to simulation mode.")
            await self._simulate()
            return

        # Build a minimal TwitchIO bot
        forward_fn = self._forward
        channel    = self.channel
        platform   = self.platform_name

        class SynthcastBot(twitch_commands.Bot):
            def __init__(self):
                super().__init__(
                    token=self.token if hasattr(self, 'token') else os.getenv("TWITCH_TOKEN", ""),
                    prefix="!",
                    initial_channels=[channel],
                )

            async def event_ready(self):
                print(f"[Twitch] Connected to #{channel} as {self.nick}")

            async def event_message(self, message):
                if message.echo:
                    return

                # Detect bits (Cheer messages)
                gift_value = 0.0
                text = message.content or ""
                if text.lower().startswith("cheer"):
                    parts = text.split()
                    for p in parts:
                        if p.lower().startswith("cheer") and p[5:].isdigit():
                            bits = int(p[5:])
                            gift_value = round(bits * 0.01, 2)  # 1 bit ≈ $0.01
                            break

                is_sub = (
                    message.author.is_subscriber
                    if hasattr(message.author, "is_subscriber") else False
                )

                await forward_fn({
                    "platform":        "twitch",
                    "username":        message.author.name,
                    "text":            text,
                    "gift_value":      gift_value,
                    "is_new_follower": False,
                    "is_subscriber":   is_sub,
                })

        bot = SynthcastBot()
        bot.token = self.token
        print(f"[Twitch] Connecting to #{self.channel}...")
        try:
            await bot.start()
        except Exception as e:
            print(f"[Twitch] Connection error: {e}")
            print("Check your TWITCH_TOKEN and TWITCH_CHANNEL in .env")

    async def _simulate(self):
        print(f"[Twitch] Simulation running")
        while self._running:
            event = random.choice(self.FAKE_EVENTS)
            await self._forward({
                "platform":        "twitch",
                "username":        event["username"],
                "text":            event["text"],
                "gift_value":      event.get("gift_value", 0.0),
                "is_new_follower": event.get("is_new_follower", False),
                "is_subscriber":   event.get("is_subscriber", False),
            })
            await asyncio.sleep(random.uniform(2.0, 5.0))


# ── YOUTUBE ───────────────────────────────────────────────────────────────────

class YouTubeListener(BasePlatformListener):
    """
    Polls YouTube Live Chat API for messages.

    Uses YouTube Data API v3 — free, 10,000 quota units/day.
    Polling every 5 seconds uses ~1 unit per poll = ~1,700 polls/day.
    More than enough for a live session.

    YOUR PART:
      1. Go to console.cloud.google.com
      2. Create a project → Enable "YouTube Data API v3"
      3. Create credentials → API Key
      4. Find your live stream's video ID from the YouTube URL
         e.g. youtube.com/watch?v=VIDEO_ID_HERE
      5. Add to .env:
           YOUTUBE_API_KEY=AIza...
           YOUTUBE_VIDEO_ID=your_live_video_id
    """

    FAKE_EVENTS = [
        {"username": "youtube_viewer",   "text": "Great stream! How do you do this?"},
        {"username": "yt_subscriber",    "text": "Been watching for months, love this"},
        {"username": "new_yt_fan",       "text": "First time here, just subscribed!",  "is_new_follower": True},
        {"username": "superchat_sender", "text": "Keep it up!", "gift_value": 10.0},
        {"username": "curious_yt_user",  "text": "What software do you use for this?"},
        {"username": "member_yt_01",     "text": "Member here, love the community",    "is_subscriber": True},
        {"username": "yt_spammer",       "text": "Sub 4 sub!! Go to my channel"},
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        video_id: Optional[str] = None,
        poll_interval_s: float = 5.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.api_key       = api_key   or os.getenv("YOUTUBE_API_KEY", "")
        self.video_id      = video_id  or os.getenv("YOUTUBE_VIDEO_ID", "")
        self.poll_interval = poll_interval_s
        self._next_page_token: Optional[str] = None

        if not self.simulation_mode:
            print(f"[YouTube] Ready for video: {self.video_id}")

    @property
    def platform_name(self) -> str:
        return "YouTube"

    async def _connect_live(self):
        if not self.api_key or not self.video_id:
            print("[YouTube] YOUTUBE_API_KEY or YOUTUBE_VIDEO_ID not set.")
            print("Falling back to simulation mode.")
            await self._simulate()
            return

        print(f"[YouTube] Connecting to live chat for video {self.video_id}...")

        # First: get the live chat ID from the video
        live_chat_id = await self._get_live_chat_id()
        if not live_chat_id:
            print(f"[YouTube] Could not find live chat for video {self.video_id}")
            print("Make sure the video is currently live and the video ID is correct.")
            return

        print(f"[YouTube] Connected. Live chat ID: {live_chat_id}")

        # Poll for new messages
        while self._running:
            await self._poll_chat(live_chat_id)
            await asyncio.sleep(self.poll_interval)

    async def _get_live_chat_id(self) -> Optional[str]:
        """Fetch the liveChatId for the given video."""
        url = "https://www.googleapis.com/youtube/v3/videos"
        params = {
            "part": "liveStreamingDetails",
            "id":   self.video_id,
            "key":  self.api_key,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", [])
                if not items:
                    return None
                return items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
        except Exception as e:
            print(f"[YouTube] Error fetching live chat ID: {e}")
            return None

    async def _poll_chat(self, live_chat_id: str):
        """Fetch new messages from YouTube Live Chat."""
        url = "https://www.googleapis.com/youtube/v3/liveChat/messages"
        params = {
            "part":       "snippet,authorDetails",
            "liveChatId": live_chat_id,
            "key":        self.api_key,
            "maxResults": 200,
        }
        if self._next_page_token:
            params["pageToken"] = self._next_page_token

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()

            self._next_page_token = data.get("nextPageToken")

            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                author  = item.get("authorDetails", {})
                msg_type = snippet.get("type", "")

                username = author.get("displayName", "viewer")
                is_member = author.get("isChatMember", False)
                is_owner  = author.get("isChatOwner", False)

                # Super Chat (paid message)
                if msg_type == "superChatEvent":
                    amount = snippet.get("superChatDetails", {}).get("amountMicros", 0)
                    gift_value = round(amount / 1_000_000, 2)
                    text = snippet.get("superChatDetails", {}).get("userComment", "Super Chat!")
                    await self._forward({
                        "platform":        "youtube",
                        "username":        username,
                        "text":            text or "sent a Super Chat!",
                        "gift_value":      gift_value,
                        "is_new_follower": False,
                        "is_subscriber":   is_member,
                    })

                # Regular message
                elif msg_type == "textMessageEvent":
                    text = snippet.get("textMessageDetails", {}).get("messageText", "")
                    if not text:
                        continue
                    await self._forward({
                        "platform":        "youtube",
                        "username":        username,
                        "text":            text,
                        "gift_value":      0.0,
                        "is_new_follower": False,
                        "is_subscriber":   is_member,
                    })

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                print("[YouTube] API quota exceeded for today. Resets at midnight PT.")
            elif e.response.status_code == 404:
                print("[YouTube] Live chat ended or video not found.")
                self._running = False
            else:
                print(f"[YouTube] API error: {e.response.status_code}")

    async def _simulate(self):
        print("[YouTube] Simulation running")
        while self._running:
            event = random.choice(self.FAKE_EVENTS)
            await self._forward({
                "platform":        "youtube",
                "username":        event["username"],
                "text":            event["text"],
                "gift_value":      event.get("gift_value", 0.0),
                "is_new_follower": event.get("is_new_follower", False),
                "is_subscriber":   event.get("is_subscriber", False),
            })
            await asyncio.sleep(random.uniform(2.5, 6.0))


# ── INSTAGRAM ────────────────────────────────────────────────────────────────

class InstagramListener(BasePlatformListener):
    """
    Instagram Live comment listener.

    IMPORTANT: Meta does not provide a public Instagram Live comment API.
    This listener runs in simulation mode only until Meta approves
    Synthcast as a platform partner (which requires app review).

    Roadmap:
      Phase 1: Simulation mode (now)
      Phase 2: Apply for Instagram Graph API Live comment access
      Phase 3: Use approved webhooks for real-time comments

    To apply for Meta Live API access:
      1. Create a Meta Developer account at developers.facebook.com
      2. Create an app → Add Instagram Graph API product
      3. Submit for App Review requesting instagram_basic +
         instagram_manage_comments permissions
      4. Once approved, add to .env:
           INSTAGRAM_ACCESS_TOKEN=...
           INSTAGRAM_LIVE_VIDEO_ID=...
    """

    FAKE_EVENTS = [
        {"username": "ig_fan_official",  "text": "This is so cool! How does this work?"},
        {"username": "insta_follower_1", "text": "Just followed! Love your content 🔥"},
        {"username": "reel_watcher",     "text": "What's your filming setup?"},
        {"username": "ig_supporter",     "text": "Been following for years, amazing!"},
        {"username": "new_ig_viewer",    "text": "First time here, came from your reel"},
        {"username": "ig_spammer_bot",   "text": "DM me for collab!! follow4follow"},
        {"username": "loyal_ig_fan",     "text": "You are literally the best 🙌"},
    ]

    def __init__(
        self,
        access_token: Optional[str] = None,
        live_video_id: Optional[str] = None,
        **kwargs
    ):
        # Force simulation mode — no public API available
        kwargs["simulation_mode"] = True
        super().__init__(**kwargs)
        self.access_token  = access_token  or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        self.live_video_id = live_video_id or os.getenv("INSTAGRAM_LIVE_VIDEO_ID", "")

        print("[Instagram] Running in simulation mode")
        print("[Instagram] Meta Live comment API requires approved partner status")
        print("[Instagram] Apply at developers.facebook.com when ready to go live")

    @property
    def platform_name(self) -> str:
        return "Instagram"

    async def _connect_live(self):
        # Not yet available — always falls to simulation
        await self._simulate()

    async def _simulate(self):
        print("[Instagram] Simulation running")
        while self._running:
            event = random.choice(self.FAKE_EVENTS)
            await self._forward({
                "platform":        "instagram",
                "username":        event["username"],
                "text":            event["text"],
                "gift_value":      event.get("gift_value", 0.0),
                "is_new_follower": event.get("is_new_follower", False),
                "is_subscriber":   event.get("is_subscriber", False),
            })
            await asyncio.sleep(random.uniform(2.0, 5.0))


# ── MULTI-PLATFORM MANAGER ────────────────────────────────────────────────────

class MultiPlatformManager:
    """
    Runs multiple platform listeners concurrently.
    One agent engine. All platforms. All at once.

    Usage:
        manager = MultiPlatformManager(
            agent_api_url="http://localhost:8000",
            platforms=["tiktok", "twitch", "youtube"],
        )
        await manager.run()
    """

    PLATFORM_MAP = {
        "twitch":    TwitchListener,
        "youtube":   YouTubeListener,
        "instagram": InstagramListener,
    }

    def __init__(
        self,
        agent_api_url: str = "http://localhost:8000",
        platforms: list[str] = None,
        simulation_mode: bool = False,
    ):
        self.agent_url = agent_api_url
        self.platforms = platforms or ["twitch", "youtube", "instagram"]
        self.simulation_mode = simulation_mode
        self._listeners: list[BasePlatformListener] = []

    def _build_listeners(self) -> list[BasePlatformListener]:
        listeners = []
        for name in self.platforms:
            cls = self.PLATFORM_MAP.get(name.lower())
            if not cls:
                print(f"[Manager] Unknown platform: {name}")
                continue
            listener = cls(
                agent_api_url=self.agent_url,
                simulation_mode=self.simulation_mode,
            )
            listeners.append(listener)
            print(f"[Manager] Added {name} listener")
        return listeners

    async def run(self):
        """Run all platform listeners concurrently."""
        self._listeners = self._build_listeners()
        if not self._listeners:
            print("[Manager] No listeners configured.")
            return

        print(f"[Manager] Starting {len(self._listeners)} platform listeners...")
        print("─" * 60)

        try:
            await asyncio.gather(*[l.start() for l in self._listeners])
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.stop()

    def stop(self):
        for l in self._listeners:
            l.stop()

    def stats(self) -> list[dict]:
        return [l.stats() for l in self._listeners]


# ── STANDALONE RUNNER ─────────────────────────────────────────────────────────

async def main():
    """Run all platform listeners in simulation mode for testing."""
    import json

    api_url = os.getenv("SYNTHCAST_API_URL", "http://localhost:8000")

    # Start a session first
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(f"{api_url}/session/start", json={
                "brand_kit": {
                    "creator_name":  os.getenv("CREATOR_NAME", "Creator"),
                    "avatar_name":   os.getenv("AVATAR_NAME", "AI Avatar"),
                    "personality":   os.getenv("CREATOR_PERSONALITY", "warm, confident, genuine"),
                    "banned_topics": ["politics", "religion"],
                    "banned_words":  [],
                    "cta_scripts":   ["Follow for more!", "Subscribe!"],
                },
                "llm_provider":   os.getenv("LLM_PROVIDER", "openai"),
                "memory_backend": "memory",
            })
            resp.raise_for_status()
            s = resp.json()
            print(f"Session: {s['session_id']} | Avatar: {s['avatar']}")
            print("─" * 60)
        except httpx.ConnectError:
            print(f"Cannot reach {api_url}")
            print("Start the API first: python -m uvicorn api.main:app --port 8000")
            return

    # Determine which platforms to run
    platforms = os.getenv("ACTIVE_PLATFORMS", "twitch,youtube,instagram").split(",")
    sim_mode  = os.getenv("SIMULATION_MODE", "true").lower() == "true"

    manager = MultiPlatformManager(
        agent_api_url=api_url,
        platforms=[p.strip() for p in platforms],
        simulation_mode=sim_mode,
    )

    try:
        await manager.run()
    except KeyboardInterrupt:
        manager.stop()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(f"{api_url}/session/stop")
                print("\nFinal stats:", json.dumps(resp.json(), indent=2))
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
