"""
synthcast/stream/tts.py

ElevenLabs Text-to-Speech wrapper.
Converts agent response text → MP3 audio bytes → plays on stream.

Phase 1: Generate audio file, play via system audio or OBS.
Phase 2: Stream audio directly into RTMP pipeline.

YOUR PART:
  1. Sign up at elevenlabs.io (free tier: 10,000 chars/mo)
  2. Set ELEVENLABS_API_KEY in your .env
  3. Run voice_setup() once to pick your voice clone or preset
"""

import os
import asyncio
import tempfile
from typing import Optional
from pathlib import Path


class TTSEngine:
    """
    Wraps ElevenLabs streaming TTS.
    Takes text → plays audio within ~300ms (streaming mode).
    """

    # Free ElevenLabs preset voices (no cloning needed to start)
    PRESET_VOICES = {
        "adam":    "pNInz6obpgDQGcFmaJgB",   # deep, authoritative male
        "bella":   "EXAVITQu4vr4xnSDxMaL",   # soft, expressive female
        "rachel":  "21m00Tcm4TlvDq8ikWAM",   # calm, clear female
        "domi":    "AZnzlk1XvdvUeBnXmlld",   # strong, confident female
        "josh":    "TxGEqnHWrfWFTfGW9XjX",   # dynamic male
        "arnold":  "VR6AewLTigWG4xSOukaG",   # crisp male
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        voice_name: str = "adam",
        model: str = "eleven_turbo_v2",       # fastest model, ~300ms
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        output_dir: Optional[str] = None,
        simulation_mode: bool = False,
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or self.PRESET_VOICES.get(voice_name, self.PRESET_VOICES["adam"])
        self.model = model
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.output_dir = Path(output_dir or tempfile.gettempdir())
        self.simulation_mode = simulation_mode or not self.api_key

        if self.simulation_mode:
            print("[TTS] Running in simulation mode — no audio will play")
        else:
            print(f"[TTS] ElevenLabs ready | voice_id={self.voice_id} | model={model}")

    async def speak(self, text: str, play: bool = True) -> Optional[bytes]:
        """
        Convert text to speech.
        Returns audio bytes. If play=True, also plays the audio.
        """
        if not text or not text.strip():
            return None

        if self.simulation_mode:
            print(f"[TTS SIM] Would speak: '{text[:80]}{'...' if len(text) > 80 else ''}'")
            await asyncio.sleep(0.3)   # simulate latency
            return b""

        try:
            from elevenlabs.client import AsyncElevenLabs
            from elevenlabs import VoiceSettings
        except ImportError:
            raise ImportError("Run: pip install elevenlabs")

        client = AsyncElevenLabs(api_key=self.api_key)

        audio_bytes = b""
        async for chunk in await client.generate(
            text=text,
            voice=self.voice_id,
            model=self.model,
            voice_settings=VoiceSettings(
                stability=self.stability,
                similarity_boost=self.similarity_boost,
                style=0.0,
                use_speaker_boost=True,
            ),
            stream=True,
        ):
            audio_bytes += chunk

        if play and audio_bytes:
            await self._play_audio(audio_bytes)

        return audio_bytes

    async def speak_and_save(self, text: str, filename: str = "response.mp3") -> Path:
        """Generate audio and save to file (for OBS or recording)."""
        audio_bytes = await self.speak(text, play=False)
        if not audio_bytes:
            return None

        output_path = self.output_dir / filename
        output_path.write_bytes(audio_bytes)
        print(f"[TTS] Saved to {output_path}")
        return output_path

    async def _play_audio(self, audio_bytes: bytes):
        """Play audio bytes through system audio."""
        try:
            # Try pygame first (cross-platform)
            import pygame
            import io
            pygame.mixer.init()
            sound = pygame.mixer.Sound(io.BytesIO(audio_bytes))
            sound.play()
            # Wait for it to finish
            duration = sound.get_length()
            await asyncio.sleep(duration)
        except ImportError:
            # Fall back to saving + system player
            tmp = self.output_dir / f"sc_tts_{int(asyncio.get_event_loop().time())}.mp3"
            tmp.write_bytes(audio_bytes)
            import subprocess
            import sys
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", str(tmp)])
            elif sys.platform == "linux":
                subprocess.Popen(["mpg123", str(tmp)])
            elif sys.platform == "win32":
                subprocess.Popen(["start", str(tmp)], shell=True)

    async def clone_voice(self, name: str, audio_file_path: str) -> str:
        """
        Clone a voice from an audio file.
        Returns the voice_id — save this to your .env as ELEVENLABS_VOICE_ID.

        audio_file_path: path to a clear 30-second+ audio sample of the creator's voice.
        """
        try:
            from elevenlabs.client import AsyncElevenLabs
        except ImportError:
            raise ImportError("Run: pip install elevenlabs")

        client = AsyncElevenLabs(api_key=self.api_key)
        with open(audio_file_path, "rb") as f:
            voice = await client.clone(
                name=name,
                description=f"Synthcast voice clone for {name}",
                files=[f],
            )
        print(f"[TTS] Voice cloned! voice_id={voice.voice_id}")
        print(f"[TTS] Add to .env: ELEVENLABS_VOICE_ID={voice.voice_id}")
        return voice.voice_id

    async def list_voices(self) -> list[dict]:
        """List all available voices on your ElevenLabs account."""
        try:
            from elevenlabs.client import AsyncElevenLabs
        except ImportError:
            raise ImportError("Run: pip install elevenlabs")

        client = AsyncElevenLabs(api_key=self.api_key)
        voices = await client.voices.get_all()
        return [{"id": v.voice_id, "name": v.name, "category": v.category} for v in voices.voices]


def get_tts_engine(**kwargs) -> TTSEngine:
    """Factory. Reads from env if not passed directly."""
    return TTSEngine(
        api_key=kwargs.get("api_key") or os.getenv("ELEVENLABS_API_KEY"),
        voice_id=kwargs.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID"),
        voice_name=kwargs.get("voice_name", "adam"),
        simulation_mode=not os.getenv("ELEVENLABS_API_KEY"),
    )
