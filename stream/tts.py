"""
synthcast/stream/tts.py
ElevenLabs TTS - updated for new SDK API
"""

import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class TTSEngine:

    PRESET_VOICES = {
        "adam":   "pNInz6obpgDQGcFmaJgB",
        "bella":  "EXAVITQu4vr4xnSDxMaL",
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "josh":   "TxGEqnHWrfWFTfGW9XjX",
    }

    def __init__(
        self,
        api_key=None,
        voice_id=None,
        voice_name="adam",
        model=os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        output_dir=None,
        simulation_mode=False,
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "") or self.PRESET_VOICES.get(voice_name, self.PRESET_VOICES["adam"])
        self.model = model
        self.output_dir = Path(output_dir or tempfile.gettempdir())
        self.simulation_mode = simulation_mode or not self.api_key

        if self.simulation_mode:
            print("[TTS] Running in simulation mode — no audio will play")
        else:
            print(f"[TTS] ElevenLabs ready | voice_id={self.voice_id} | model={model}")

    async def speak(self, text: str, play: bool = True) -> Optional[bytes]:
        if not text or not text.strip():
            return None

        if self.simulation_mode:
            print(f"[TTS SIM] Would speak: '{text[:80]}{'...' if len(text) > 80 else ''}'")
            await asyncio.sleep(0.3)
            return b""

        try:
            from elevenlabs.client import AsyncElevenLabs
        except ImportError:
            raise ImportError("Run: pip install elevenlabs")

        client = AsyncElevenLabs(api_key=self.api_key)

        audio_bytes = b""
        async for chunk in client.text_to_speech.convert(
            text=text,
            voice_id=self.voice_id,
            model_id=self.model,
        ):
            audio_bytes += chunk

        if play and audio_bytes:
            await self._play_audio(audio_bytes)

        return audio_bytes

    async def speak_and_save(self, text: str, filename: str = "response.mp3") -> Path:
        audio_bytes = await self.speak(text, play=False)
        if not audio_bytes:
            return None
        output_path = self.output_dir / filename
        output_path.write_bytes(audio_bytes)
        return output_path

    async def _play_audio(self, audio_bytes: bytes):
        # Save to temp file and play with system player
        tmp = self.output_dir / f"sc_tts_{int(asyncio.get_event_loop().time() * 1000)}.mp3"
        tmp.write_bytes(audio_bytes)

        try:
            import pygame
            import io
            pygame.mixer.init()
            sound = pygame.mixer.Sound(io.BytesIO(audio_bytes))
            sound.play()
            await asyncio.sleep(sound.get_length() + 0.2)
        except Exception:
            # Fallback to system player
            import sys
            if sys.platform == "win32":
                subprocess.Popen(["start", str(tmp)], shell=True)
                # Estimate duration based on word count
                words = len(audio_bytes) // 1000
                await asyncio.sleep(max(2, words * 0.4))

    async def clone_voice(self, name: str, audio_file_path: str) -> str:
        from elevenlabs.client import AsyncElevenLabs
        client = AsyncElevenLabs(api_key=self.api_key)
        with open(audio_file_path, "rb") as f:
            voice = await client.voices.add(
                name=name,
                files=[f],
            )
        print(f"[TTS] Voice cloned! voice_id={voice.voice_id}")
        print(f"[TTS] Add to .env: ELEVENLABS_VOICE_ID={voice.voice_id}")
        return voice.voice_id

    async def list_voices(self) -> list:
        from elevenlabs.client import AsyncElevenLabs
        client = AsyncElevenLabs(api_key=self.api_key)
        voices = await client.voices.get_all()
        return [{"id": v.voice_id, "name": v.name} for v in voices.voices]


def get_tts_engine(**kwargs) -> TTSEngine:
    return TTSEngine(
        api_key=kwargs.get("api_key") or os.getenv("ELEVENLABS_API_KEY"),
        voice_id=kwargs.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID"),
        voice_name=kwargs.get("voice_name", "adam"),
        simulation_mode=not os.getenv("ELEVENLABS_API_KEY"),
    )
