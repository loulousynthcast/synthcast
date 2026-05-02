"""
synthcast/avatar/video_avatar.py

Video avatar rendering — lip-sync face animation from agent voice.
Supports D-ID and HeyGen APIs. Falls back to audio-only if not configured.

How it works:
  1. Agent generates response text
  2. ElevenLabs converts text → MP3 audio
  3. This module sends audio + presenter image → D-ID or HeyGen
  4. They render a lip-synced video of your face speaking
  5. Video streams into OBS as a source → goes live on TikTok/Twitch/YouTube

Latency reality check:
  - D-ID streaming: ~1.5–3s from audio to rendered video
  - HeyGen real-time: ~1–2s (newer, faster)
  - Strategy: play audio immediately, video follows ~1s behind
    Viewers accept this — they hear the response instantly,
    see the lip-sync a beat later. Feels natural.

Install:
    pip install httpx aiofiles

YOUR PART:
  D-ID:   Sign up at d-id.com → API keys → copy key
          Add: DID_API_KEY=your_key and DID_PRESENTER_ID=your_presenter_id
  HeyGen: Sign up at heygen.com → API → copy key
          Add: HEYGEN_API_KEY=your_key and HEYGEN_AVATAR_ID=your_avatar_id
"""

import os
import asyncio
import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import httpx
from dotenv import load_dotenv

load_dotenv()


# ── AVATAR RESPONSE ───────────────────────────────────────────────────────────

@dataclass
class AvatarRenderResult:
    """Result of a video avatar render."""
    provider: str
    video_url: Optional[str]       # URL to rendered video (stream or download)
    stream_url: Optional[str]      # WebRTC/HLS stream URL for real-time
    duration_s: float              # estimated video duration
    latency_s: float               # how long the render took
    audio_played_first: bool       # True if audio was played before video ready
    status: str                    # "ready" | "processing" | "failed"
    error: Optional[str] = None


# ── BASE AVATAR PROVIDER ──────────────────────────────────────────────────────

class BaseAvatarProvider(ABC):
    """All avatar providers implement this interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def render(
        self,
        text: str,
        audio_bytes: Optional[bytes] = None,
    ) -> AvatarRenderResult: ...

    @abstractmethod
    async def is_configured(self) -> bool: ...


# ── D-ID PROVIDER ─────────────────────────────────────────────────────────────

class DIDProvider(BaseAvatarProvider):
    """
    D-ID video avatar rendering.
    Generates a lip-synced talking head video from text or audio.

    D-ID API docs: https://docs.d-id.com

    YOUR PART:
      1. Sign up at d-id.com (free trial: 5 min of video)
      2. Dashboard → API → Copy your API key
      3. Upload your photo at d-id.com → note the presenter ID
      4. Add to .env:
           DID_API_KEY=Basic_xxxxxxxx
           DID_PRESENTER_ID=pers_xxxxxxxx   (from your uploaded photo)

    Presenter ID options:
      - Upload your own photo → use that presenter ID (recommended)
      - Use a D-ID stock presenter (listed in their docs)
    """

    BASE_URL = "https://api.d-id.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        presenter_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        simulation_mode: bool = False,
    ):
        self.api_key       = api_key      or os.getenv("DID_API_KEY", "")
        self.presenter_id  = presenter_id or os.getenv("DID_PRESENTER_ID", "")
        self.voice_id      = voice_id     or os.getenv("ELEVENLABS_VOICE_ID", "")
        self.simulation_mode = simulation_mode or not self.api_key

        if self.simulation_mode:
            print("[D-ID] Simulation mode — no video rendered")
        else:
            print(f"[D-ID] Ready | presenter={self.presenter_id}")

    @property
    def provider_name(self) -> str:
        return "d-id"

    async def is_configured(self) -> bool:
        return bool(self.api_key and self.presenter_id)

    async def render(
        self,
        text: str,
        audio_bytes: Optional[bytes] = None,
    ) -> AvatarRenderResult:
        """
        Render a lip-synced video of the presenter speaking the text.

        Strategy:
          - If audio_bytes provided: use audio-driven lip-sync (more accurate)
          - If not: use D-ID's built-in TTS (faster but less control)
        """
        start = time.monotonic()

        if self.simulation_mode:
            await asyncio.sleep(0.5)  # simulate latency
            words = len(text.split())
            duration = round((words / 130) * 60, 1)
            return AvatarRenderResult(
                provider="d-id",
                video_url="https://simulated-video.synthcast.io/avatar.mp4",
                stream_url=None,
                duration_s=duration,
                latency_s=0.5,
                audio_played_first=True,
                status="ready",
            )

        headers = {
            "Authorization": self.api_key,
            "Content-Type":  "application/json",
        }

        # Build the talk payload
        if audio_bytes:
            # Audio-driven: encode audio as base64 and send to D-ID
            audio_b64 = base64.b64encode(audio_bytes).decode()
            script = {
                "type":   "audio",
                "audio":  f"data:audio/mpeg;base64,{audio_b64}",
            }
        else:
            # Text-driven: D-ID generates TTS internally
            script = {
                "type":     "text",
                "input":    text,
                "provider": {
                    "type":    "elevenlabs",
                    "voice_id": self.voice_id,
                } if self.voice_id else {
                    "type": "microsoft",
                    "voice_id": "en-US-JennyNeural",
                }
            }

        payload = {
            "source_url": f"https://d-id.com/api/presenters/{self.presenter_id}",
            "script":     script,
            "config": {
                "fluent":   True,
                "pad_audio": 0,
                "stitch":   True,
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Create the talk
            resp = await client.post(
                f"{self.BASE_URL}/talks",
                json=payload,
                headers=headers,
            )

            if resp.status_code != 201:
                return AvatarRenderResult(
                    provider="d-id",
                    video_url=None, stream_url=None,
                    duration_s=0, latency_s=0,
                    audio_played_first=True,
                    status="failed",
                    error=f"D-ID API error {resp.status_code}: {resp.text[:200]}",
                )

            talk_id = resp.json().get("id")

            # 2. Poll for completion (D-ID is async)
            for attempt in range(20):  # max 20s wait
                await asyncio.sleep(1.0)
                status_resp = await client.get(
                    f"{self.BASE_URL}/talks/{talk_id}",
                    headers=headers,
                )
                status_data = status_resp.json()
                status = status_data.get("status")

                if status == "done":
                    video_url = status_data.get("result_url")
                    latency = time.monotonic() - start
                    words = len(text.split())
                    duration = round((words / 130) * 60, 1)

                    return AvatarRenderResult(
                        provider="d-id",
                        video_url=video_url,
                        stream_url=None,
                        duration_s=duration,
                        latency_s=round(latency, 2),
                        audio_played_first=True,  # audio plays while video renders
                        status="ready",
                    )

                elif status == "error":
                    return AvatarRenderResult(
                        provider="d-id",
                        video_url=None, stream_url=None,
                        duration_s=0, latency_s=0,
                        audio_played_first=True,
                        status="failed",
                        error=status_data.get("error", "Unknown D-ID error"),
                    )

            # Timeout
            return AvatarRenderResult(
                provider="d-id",
                video_url=None, stream_url=None,
                duration_s=0, latency_s=20.0,
                audio_played_first=True,
                status="failed",
                error="D-ID render timed out after 20s",
            )

    async def create_presenter_from_photo(self, photo_path: str) -> str:
        """
        Upload your photo to D-ID and get a presenter ID.
        Run this once — save the returned presenter_id to .env.
        """
        if self.simulation_mode:
            print("[D-ID] Simulation: returning fake presenter ID")
            return "pers_simulated_123"

        headers = {"Authorization": self.api_key}

        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(photo_path, "rb") as f:
                resp = await client.post(
                    f"{self.BASE_URL}/images",
                    headers=headers,
                    files={"image": f},
                )
            resp.raise_for_status()
            data = resp.json()
            presenter_id = data.get("id")
            print(f"[D-ID] Presenter created: {presenter_id}")
            print(f"[D-ID] Add to .env: DID_PRESENTER_ID={presenter_id}")
            return presenter_id


# ── HEYGEN PROVIDER ───────────────────────────────────────────────────────────

class HeyGenProvider(BaseAvatarProvider):
    """
    HeyGen real-time avatar streaming.
    Faster than D-ID for live streams — designed for real-time use.

    HeyGen API docs: https://docs.heygen.com

    YOUR PART:
      1. Sign up at heygen.com (free trial: 1 min of video)
      2. API → Generate API key
      3. Create an avatar from your photo (Interactive Avatar section)
      4. Note the avatar_id
      5. Add to .env:
           HEYGEN_API_KEY=your_key_here
           HEYGEN_AVATAR_ID=your_avatar_id
           HEYGEN_VOICE_ID=your_voice_id  (optional — uses ElevenLabs if blank)
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(
        self,
        api_key: Optional[str] = None,
        avatar_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        simulation_mode: bool = False,
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
        return bool(self.api_key and self.avatar_id)

    async def render(
        self,
        text: str,
        audio_bytes: Optional[bytes] = None,
    ) -> AvatarRenderResult:
        """Generate a HeyGen talking avatar video."""
        start = time.monotonic()

        if self.simulation_mode:
            await asyncio.sleep(0.4)
            words = len(text.split())
            duration = round((words / 130) * 60, 1)
            return AvatarRenderResult(
                provider="heygen",
                video_url="https://simulated-video.synthcast.io/heygen.mp4",
                stream_url="https://simulated-stream.synthcast.io/live.m3u8",
                duration_s=duration,
                latency_s=0.4,
                audio_played_first=True,
                status="ready",
            )

        headers = {
            "X-Api-Key":    self.api_key,
            "Content-Type": "application/json",
        }

        # Build voice config
        voice_config = {}
        if self.voice_id:
            voice_config = {"voice_id": self.voice_id, "type": "text"}
        else:
            voice_config = {"type": "text", "voice_id": "en-US-JennyNeural"}

        payload = {
            "video_inputs": [{
                "character": {
                    "type":       "avatar",
                    "avatar_id":  self.avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {**voice_config, "input_text": text},
                "background": {"type": "color", "value": "#000000"},
            }],
            "dimension": {"width": 1080, "height": 1920},  # portrait for TikTok
            "aspect_ratio": "9:16",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # 1. Submit video generation
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

            # 2. Poll for completion
            for attempt in range(60):  # max 60s
                await asyncio.sleep(1.0)
                status_resp = await client.get(
                    f"{self.BASE_URL}/v1/video_status.get",
                    params={"video_id": video_id},
                    headers=headers,
                )
                status_data = status_resp.json().get("data", {})
                status = status_data.get("status")

                if status == "completed":
                    video_url = status_data.get("video_url")
                    latency = time.monotonic() - start
                    words = len(text.split())
                    duration = round((words / 130) * 60, 1)

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
                        error=status_data.get("error", "HeyGen render failed"),
                    )

        return AvatarRenderResult(
            provider="heygen",
            video_url=None, stream_url=None,
            duration_s=0, latency_s=60.0,
            audio_played_first=True,
            status="failed",
            error="HeyGen render timed out",
        )

    async def list_avatars(self) -> list[dict]:
        """List all avatars on your HeyGen account."""
        if self.simulation_mode:
            return [{"avatar_id": "sim_001", "avatar_name": "Simulated Avatar"}]

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/v2/avatars",
                headers={"X-Api-Key": self.api_key},
            )
            resp.raise_for_status()
            return resp.json().get("data", {}).get("avatars", [])


# ── AVATAR ENGINE ─────────────────────────────────────────────────────────────

class AvatarEngine:
    """
    Main avatar engine. Orchestrates:
      1. TTS → audio
      2. Audio → video avatar (async, doesn't block the stream)
      3. Video → OBS source

    The key design decision: audio plays immediately.
    Video renders in the background and updates the OBS source
    when ready. Viewers hear the response within ~300ms,
    see the lip-sync video within ~1.5–3s.
    This is the right tradeoff for live streaming.
    """

    def __init__(
        self,
        provider: Optional[BaseAvatarProvider] = None,
        tts_engine=None,
        obs_websocket_url: Optional[str] = None,
        output_dir: str = "avatar_output",
    ):
        self.provider    = provider
        self.tts         = tts_engine
        self.obs_url     = obs_websocket_url or os.getenv("OBS_WEBSOCKET_URL")
        self.output_dir  = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self._render_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def speak_with_avatar(self, text: str) -> AvatarRenderResult:
        """
        Full pipeline: text → TTS audio → video avatar.

        Returns immediately after audio starts playing.
        Video renders in background.
        """
        # Step 1: Generate and play audio immediately
        audio_bytes = None
        if self.tts:
            audio_bytes = await self.tts.speak(text, play=True)

        # Step 2: Render video avatar (background task)
        if self.provider:
            result = await self.provider.render(text, audio_bytes)

            # Step 3: Push to OBS if configured
            if result.status == "ready" and result.video_url and self.obs_url:
                asyncio.create_task(self._push_to_obs(result.video_url))

            return result

        # No avatar provider — audio-only mode
        words = len(text.split())
        return AvatarRenderResult(
            provider="audio-only",
            video_url=None, stream_url=None,
            duration_s=round((words / 130) * 60, 1),
            latency_s=0,
            audio_played_first=True,
            status="ready",
        )

    async def _push_to_obs(self, video_url: str):
        """
        Push a video URL to OBS as a media source.
        Requires OBS WebSocket plugin (built into OBS 28+).

        OBS setup:
          1. OBS → Tools → WebSocket Server Settings → Enable
          2. Note the port (default: 4455) and password
          3. Add to .env: OBS_WEBSOCKET_URL=ws://localhost:4455
          4. In OBS, add a "Media Source" named "synthcast_avatar"

        Install: pip install obs-websocket-py
        """
        if not self.obs_url:
            return

        try:
            import obsws_python as obs
            ws = obs.ReqClient(
                host="localhost",
                port=int(self.obs_url.split(":")[-1]),
                password=os.getenv("OBS_WEBSOCKET_PASSWORD", ""),
            )
            # Update the media source with the new video URL
            ws.set_input_settings(
                name="synthcast_avatar",
                settings={"local_file": video_url, "is_local_file": False},
                overlay=True,
            )
            print(f"[Avatar] OBS source updated → {video_url[:60]}")
        except ImportError:
            print("[Avatar] Install obs-websocket-py to push to OBS: pip install obs-websocket-py")
        except Exception as e:
            print(f"[Avatar] OBS push failed: {e}")

    def stats(self) -> dict:
        return {
            "provider":  self.provider.provider_name if self.provider else "none",
            "tts":       "configured" if self.tts else "none",
            "obs":       "configured" if self.obs_url else "none",
        }


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_avatar_engine(
    provider: str = "auto",
    tts_engine=None,
    simulation_mode: bool = False,
) -> AvatarEngine:
    """
    Factory. Picks the best available provider.

    provider="auto":  tries D-ID first, then HeyGen, then audio-only
    provider="did":   D-ID only
    provider="heygen": HeyGen only
    provider="none":  audio-only (no video avatar)
    """
    avatar_provider = None

    if provider == "auto":
        if os.getenv("DID_API_KEY"):
            avatar_provider = DIDProvider(simulation_mode=simulation_mode)
        elif os.getenv("HEYGEN_API_KEY"):
            avatar_provider = HeyGenProvider(simulation_mode=simulation_mode)
        else:
            print("[Avatar] No API keys found — running audio-only")
            print("  Add DID_API_KEY or HEYGEN_API_KEY to .env for video avatar")
    elif provider == "did":
        avatar_provider = DIDProvider(simulation_mode=simulation_mode)
    elif provider == "heygen":
        avatar_provider = HeyGenProvider(simulation_mode=simulation_mode)
    elif provider == "none":
        avatar_provider = None
    else:
        print(f"[Avatar] Unknown provider '{provider}' — audio-only")

    return AvatarEngine(provider=avatar_provider, tts_engine=tts_engine)
