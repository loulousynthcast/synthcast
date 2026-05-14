"""
synthcast/avatar/video_avatar.py

Video avatar rendering — lip-sync face animation from agent voice.
Supports HeyGen API. Falls back to audio-only if not configured.

Tested and working with:
  HEYGEN_AVATAR_ID=fb47deecbdbb4ef3bf6ab784e904c7dc
  HEYGEN_VOICE_ID=9f71536fae294bbda05197e87aeed8f3

Install:
    pip install httpx
"""

import os
import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AvatarRenderResult:
    provider: str
    video_url: Optional[str]
    stream_url: Optional[str]
    duration_s: float
    latency_s: float
    audio_played_first: bool
    status: str
    error: Optional[str] = None


class BaseAvatarProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def render(self, text: str, audio_bytes=None) -> AvatarRenderResult: ...

    @abstractmethod
    async def is_configured(self) -> bool: ...


class HeyGenProvider(BaseAvatarProvider):
    """
    HeyGen video avatar rendering.
    Tested and working — renders lip-synced video in ~30-60 seconds.

    Correct API format discovered through testing:
      avatar_id = your look ID (fb47deec...)
      voice_id  = your cloned voice ID
      No avatar_look_id parameter needed
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(
        self,
        api_key=None,
        avatar_id=None,
        voice_id=None,
        simulation_mode=False,
    ):
        self.api_key     = api_key   or os.getenv("HEYGEN_API_KEY", "")
        self.avatar_id   = avatar_id or os.getenv("HEYGEN_AVATAR_ID", "")
        self.voice_id    = voice_id  or os.getenv("HEYGEN_VOICE_ID", "")
        self.simulation_mode = simulation_mode or not self.api_key

        if self.simulation_mode:
            print("[HeyGen] Simulation mode — no video rendered")
        else:
            print(f"[HeyGen] Ready | avatar={self.avatar_id}")

    @property
    def provider_name(self) -> str:
        return "heygen"

    async def is_configured(self) -> bool:
        return bool(self.api_key and self.avatar_id and self.voice_id)

    async def render(self, text: str, audio_bytes=None) -> AvatarRenderResult:
        start = time.monotonic()

        if self.simulation_mode:
            await asyncio.sleep(0.4)
            words = len(text.split())
            duration = round((words / 130) * 60, 1)
            return AvatarRenderResult(
                provider="heygen",
                video_url="https://simulated-video.synthcast.io/heygen.mp4",
                stream_url=None,
                duration_s=duration,
                latency_s=0.4,
                audio_played_first=True,
                status="ready",
            )

        headers = {
            "X-Api-Key":    self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "video_inputs": [{
                "character": {
                    "type":         "avatar",
                    "avatar_id":    self.avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type":       "text",
                    "input_text": text,
                    "voice_id":   self.voice_id,
                },
                "background": {
                    "type":  "color",
                    "value": "#0D0D0F",
                },
            }],
            "dimension": {"width": 1080, "height": 1920},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Submit render
            resp = await client.post(
                f"{self.BASE_URL}/v2/video/generate",
                json=payload,
                headers=headers,
            )

            if resp.status_code != 200:
                return AvatarRenderResult(
                    provider="heygen",
                    video_url=None, stream_url=None,
                    duration_s=0, latency_s=0,
                    audio_played_first=True,
                    status="failed",
                    error=f"HeyGen error {resp.status_code}: {resp.text[:200]}",
                )

            video_id = resp.json().get("data", {}).get("video_id")
            print(f"[HeyGen] Rendering video_id={video_id}")

            # Poll for completion
            for attempt in range(60):
                await asyncio.sleep(3)
                poll = await client.get(
                    f"{self.BASE_URL}/v1/video_status.get",
                    params={"video_id": video_id},
                    headers={"X-Api-Key": self.api_key},
                )
                data = poll.json().get("data", {})
                status = data.get("status")

                if status == "completed":
                    video_url = data.get("video_url")
                    latency = time.monotonic() - start
                    words = len(text.split())
                    duration = round((words / 130) * 60, 1)
                    print(f"[HeyGen] Completed in {round(latency, 1)}s")
                    return AvatarRenderResult(
                        provider="heygen",
                        video_url=video_url,
                        stream_url=None,
                        duration_s=duration,
                        latency_s=round(latency, 2),
                        audio_played_first=True,
                        status="ready",
                    )

                elif status == "failed":
                    return AvatarRenderResult(
                        provider="heygen",
                        video_url=None, stream_url=None,
                        duration_s=0, latency_s=0,
                        audio_played_first=True,
                        status="failed",
                        error=str(data.get("error", "HeyGen render failed")),
                    )

        return AvatarRenderResult(
            provider="heygen",
            video_url=None, stream_url=None,
            duration_s=0, latency_s=180.0,
            audio_played_first=True,
            status="failed",
            error="HeyGen render timed out",
        )

    async def list_avatars(self) -> list:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/v2/avatars",
                headers={"X-Api-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("avatars", [])

    async def list_voices(self) -> list:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/v2/voices",
                headers={"X-Api-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("voices", [])


class AvatarEngine:
    """
    Orchestrates: TTS audio → HeyGen video → OBS source update.

    Strategy: play audio immediately, render video in background.
    Viewer hears response in ~300ms, sees lip-sync in ~35 seconds.
    """

    def __init__(self, provider=None, tts_engine=None, obs_websocket_url=None):
        self.provider = provider
        self.tts      = tts_engine
        self.obs_url  = obs_websocket_url or os.getenv("OBS_WEBSOCKET_URL")

    async def speak_with_avatar(self, text: str) -> AvatarRenderResult:
        # 1. Play audio immediately
        audio_bytes = None
        if self.tts:
            audio_bytes = await self.tts.speak(text, play=True)

        # 2. Render video in background
        if self.provider:
            result = await self.provider.render(text, audio_bytes)
            if result.status == "ready" and result.video_url:
                print(f"[Avatar] Video ready: {result.video_url[:60]}...")
                if self.obs_url:
                    asyncio.create_task(self._push_to_obs(result.video_url))
            return result

        words = len(text.split())
        return AvatarRenderResult(
            provider="audio-only",
            video_url=None, stream_url=None,
            duration_s=round((words / 130) * 60, 1),
            latency_s=0, audio_played_first=True, status="ready",
        )

    async def _push_to_obs(self, video_url: str):
        try:
            import obsws_python as obs
            port = int(self.obs_url.split(":")[-1])
            ws = obs.ReqClient(
                host="localhost", port=port,
                password=os.getenv("OBS_WEBSOCKET_PASSWORD", ""),
            )
            ws.set_input_settings(
                name="synthcast_avatar",
                settings={"local_file": video_url, "is_local_file": False},
                overlay=True,
            )
            print(f"[Avatar] OBS updated")
        except Exception as e:
            print(f"[Avatar] OBS push failed: {e}")

    def stats(self) -> dict:
        return {
            "provider": self.provider.provider_name if self.provider else "none",
            "tts":      "configured" if self.tts else "none",
            "obs":      "configured" if self.obs_url else "none",
        }


def get_avatar_engine(provider="auto", tts_engine=None, simulation_mode=False) -> AvatarEngine:
    avatar_provider = None

    if provider == "auto":
        if os.getenv("HEYGEN_API_KEY"):
            avatar_provider = HeyGenProvider(simulation_mode=simulation_mode)
        else:
            print("[Avatar] No API keys found — audio-only mode")
    elif provider == "heygen":
        avatar_provider = HeyGenProvider(simulation_mode=simulation_mode)
    elif provider == "none":
        avatar_provider = None

    return AvatarEngine(provider=avatar_provider, tts_engine=tts_engine)


# D-ID stub for compatibility
class DIDProvider(BaseAvatarProvider):
    def __init__(self, **kwargs):
        print("[D-ID] Not configured — use HeyGen instead")
        self.simulation_mode = True

    @property
    def provider_name(self): return "d-id"

    async def is_configured(self): return False

    async def render(self, text, audio_bytes=None):
        return AvatarRenderResult(
            provider="d-id", video_url=None, stream_url=None,
            duration_s=0, latency_s=0, audio_played_first=True,
            status="failed", error="D-ID not configured"
        )
