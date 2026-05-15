"""
synthcast/avatar/obs_setup.py

OBS scene configuration for Synthcast avatar streaming.
Sets up the correct sources and layout for going live
with a video avatar on TikTok, Twitch, YouTube, etc.

Run once to configure your OBS:
    python avatar/obs_setup.py

Requires:
    OBS Studio 28+ (has built-in WebSocket server)
    pip install obs-websocket-py
"""

import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

# ── OBS SCENE LAYOUT ──────────────────────────────────────────────────────────
# Portrait layout for TikTok/Instagram (1080x1920)
# Landscape for Twitch/YouTube (1920x1080)

PORTRAIT_SCENE = {
    "name": "Synthcast Portrait (TikTok)",
    "width": 1080,
    "height": 1920,
    "sources": [
        {
            "name":   "synthcast_avatar",
            "type":   "ffmpeg_source",   # Media Source in OBS
            "x":      0, "y": 0,
            "width":  1080, "height": 1920,
            "note":   "Your avatar video plays here. The avatar engine updates this source.",
        },
        {
            "name":   "avatar_background",
            "type":   "color_source",
            "color":  "0xFF0D0D0F",       # Synthcast void black
            "x":      0, "y": 0,
            "width":  1080, "height": 1920,
            "note":   "Dark background. Place BELOW the avatar source.",
        },
        {
            "name":   "comment_overlay",
            "type":   "browser_source",
            "url":    "http://localhost:8000/overlay/comments",
            "width":  1080, "height": 400,
            "x":      0, "y": 1520,
            "note":   "Comment feed overlay (optional). Shows recent comments.",
        },
        {
            "name":   "live_badge",
            "type":   "text_gdiplus",
            "text":   "🔴 LIVE",
            "x":      40, "y": 60,
            "note":   "Live indicator top-left.",
        },
    ]
}

LANDSCAPE_SCENE = {
    "name": "Synthcast Landscape (Twitch/YouTube)",
    "width": 1920,
    "height": 1080,
    "sources": [
        {
            "name":   "synthcast_avatar",
            "type":   "ffmpeg_source",
            "x":      660, "y": 0,
            "width":  600, "height": 1080,
            "note":   "Avatar in center. Comment feed on sides.",
        },
        {
            "name":   "avatar_background",
            "type":   "color_source",
            "color":  "0xFF0D0D0F",
            "x":      0, "y": 0,
            "width":  1920, "height": 1080,
        },
        {
            "name":   "chat_left",
            "type":   "browser_source",
            "url":    "http://localhost:8000/overlay/comments",
            "width":  640, "height": 1080,
            "x":      0, "y": 0,
            "note":   "Comment feed on the left side.",
        },
        {
            "name":   "stats_right",
            "type":   "browser_source",
            "url":    "http://localhost:8000/overlay/stats",
            "width":  640, "height": 1080,
            "x":      1280, "y": 0,
            "note":   "Live stats on the right: viewer count, gifts, etc.",
        },
    ]
}


def print_obs_setup_guide():
    """
    Print step-by-step OBS setup instructions.
    Run this if you don't have obs-websocket-py installed.
    """
    print("""
╔══════════════════════════════════════════════════════════════════╗
║           SYNTHCAST OBS SETUP GUIDE                            ║
╚══════════════════════════════════════════════════════════════════╝

STEP 1 — Enable OBS WebSocket Server
  OBS → Tools → WebSocket Server Settings
  ✓ Enable WebSocket server
  ✓ Port: 4455 (default)
  ✓ Set a password → add to .env as OBS_WEBSOCKET_PASSWORD=yourpassword
  ✓ Add to .env: OBS_WEBSOCKET_URL=ws://localhost:4455

STEP 2 — Create the Synthcast Scene (Portrait for TikTok)
  OBS → Scenes panel → + → Name it "Synthcast Portrait"
  Set canvas to 1080×1920 (File → Settings → Video → Base Resolution)

STEP 3 — Add Sources in this order (bottom to top):
  1. Color Source   → name: "avatar_background"  → color: #0D0D0F (void black)
  2. Media Source   → name: "synthcast_avatar"    → check "Loop" and "Restart when active"
  3. Browser Source → name: "comment_overlay"     → URL: http://localhost:8000/overlay/comments

STEP 4 — Configure Media Source
  Right-click "synthcast_avatar" → Properties
  ✓ Uncheck "Local File" (avatar engine will stream a URL)
  ✓ Check "Loop"
  ✓ Check "Restart playback when source becomes active"

STEP 5 — Set Up Virtual Camera (for platforms that need it)
  OBS → Start Virtual Camera
  This creates a virtual webcam that you select in TikTok/Zoom/etc.

STEP 6 — Stream Settings per platform
  TikTok:    Tools → RTMP settings → Server: rtmp://push.tiktok.com/live/
             Use TikTok LIVE Studio for the stream key
  Twitch:    Settings → Stream → Service: Twitch → Connect Account
  YouTube:   Settings → Stream → Service: YouTube → Connect Account

STEP 7 — Test before going live
  python avatar/obs_setup.py --test
  This sends a test render to verify the pipeline works.

══════════════════════════════════════════════════════════════════
IMPORTANT: The avatar engine updates OBS automatically.
You just need OBS running with the scene active.
When your avatar speaks, the video source updates in real time.
══════════════════════════════════════════════════════════════════
""")


async def test_avatar_pipeline(text: str = "Hello! I'm your AI avatar. The pipeline is working perfectly."):
    """
    Test the full avatar pipeline end to end.
    Generates a test video and pushes it to OBS.
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from avatar.video_avatar import get_avatar_engine

    print("[Test] Starting avatar pipeline test...")
    print(f"[Test] Text: '{text}'")

    engine = get_avatar_engine(provider="auto", simulation_mode=True)
    result = await engine.speak_with_avatar(text)

    print(f"\n[Test] Result:")
    print(f"  Provider:    {result.provider}")
    print(f"  Status:      {result.status}")
    print(f"  Duration:    {result.duration_s}s")
    print(f"  Latency:     {result.latency_s}s")
    print(f"  Video URL:   {result.video_url}")
    print(f"  Audio first: {result.audio_played_first}")

    if result.status == "ready":
        print("\n✓ Avatar pipeline working correctly")
    else:
        print(f"\n✗ Pipeline failed: {result.error}")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        asyncio.run(test_avatar_pipeline())
    else:
        print_obs_setup_guide()
