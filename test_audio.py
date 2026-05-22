"""
test_audio.py
Test that ElevenLabs TTS audio is routing correctly through OBS.
Run this before going live to verify your setup.
"""

import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_KEY   = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "")
API_URL          = os.getenv("API_URL", "https://synthcast-production.up.railway.app")
CREATOR_ID       = os.getenv("CREATOR_ID", "louguens")

TEST_PHRASES = [
    "Synthcast audio test — one, two, three. If you can hear this in OBS, your routing is working.",
    "Hey! Your AI avatar is connected and ready to go live.",
    "Comment feed is active. Viewers can now hear my responses.",
]


async def test_elevenlabs():
    """Test ElevenLabs TTS."""
    if not ELEVENLABS_KEY or not ELEVENLABS_VOICE:
        print("❌ ElevenLabs not configured — check ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env")
        return False

    print("Testing ElevenLabs TTS...")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}",
                headers={"xi-api-key": ELEVENLABS_KEY},
                json={
                    "text": TEST_PHRASES[0],
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
                }
            )
            if resp.status_code == 200:
                import tempfile
                audio_path = os.path.join(tempfile.gettempdir(), "synthcast_test.mp3")
                with open(audio_path, "wb") as f:
                    f.write(resp.content)
                print("✅ ElevenLabs working — playing test audio now...")
                if os.name == 'nt':  # Windows
                    os.system(f'start "" "{audio_path}"')
                else:
                    os.system(f"ffplay -nodisp -autoexit '{audio_path}' 2>/dev/null || afplay '{audio_path}' 2>/dev/null")
                return True
            else:
                print(f"❌ ElevenLabs error: {resp.status_code} — {resp.text[:200]}")
                return False
    except Exception as e:
        print(f"❌ ElevenLabs connection failed: {e}")
        return False


async def test_api():
    """Test Railway API connection."""
    print("Testing Railway API...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{API_URL}/health")
            if resp.status_code == 200:
                print(f"✅ Railway API online: {API_URL}")
                return True
            else:
                print(f"❌ Railway API error: {resp.status_code}")
                return False
    except Exception as e:
        print(f"❌ Railway API unreachable: {e}")
        return False


async def test_stream_api():
    """Test stream push to dashboard."""
    print("Testing stream API (dashboard connection)...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Push a test comment
            resp = await client.post(
                f"{API_URL}/stream/comment?creator_id={CREATOR_ID}",
                json={
                    "username": "SynthcastTest",
                    "text": "Audio test in progress",
                    "platform": "Test",
                    "comment_type": "C",
                    "response": "Audio routing test successful!",
                }
            )
            if resp.status_code == 200:
                print("✅ Dashboard comment push working")
                print("   → Open synthcast.live/app and click Go Live to see it")
                return True
            else:
                print(f"❌ Dashboard push failed: {resp.status_code}")
                return False
    except Exception as e:
        print(f"❌ Dashboard push failed: {e}")
        return False


async def main():
    print("=" * 50)
    print("SYNTHCAST AUDIO ROUTING TEST")
    print("=" * 50)
    print()

    results = []
    results.append(await test_api())
    results.append(await test_elevenlabs())
    results.append(await test_stream_api())

    print()
    print("=" * 50)
    if all(results):
        print("✅ ALL SYSTEMS GO — You are ready to stream")
        print()
        print("Next steps:")
        print("1. Open OBS — check AI Voice audio level is moving")
        print("2. Set YOUTUBE_VIDEO_ID=your_live_id in .env")
        print("3. Run: python youtube_live_listener.py")
        print("4. Start streaming in OBS")
        print("5. Open synthcast.live/app → click Go Live")
    else:
        print("⚠️  Some tests failed — fix the issues above before going live")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
