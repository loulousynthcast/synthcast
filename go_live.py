"""
go_live.py
Launch all Synthcast platform listeners simultaneously.
Run this instead of running each listener separately.

Usage:
  python go_live.py           — YouTube + Twitch
  python go_live.py youtube   — YouTube only
  python go_live.py twitch    — Twitch only
"""

import os
import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()


async def run_youtube():
    from youtube_live_listener import main as youtube_main
    print("[Synthcast] Starting YouTube listener...")
    await youtube_main()


async def run_twitch():
    from twitch_listener import main as twitch_main
    print("[Synthcast] Starting Twitch listener...")
    await twitch_main()


async def main():
    args = sys.argv[1:]
    platform = args[0].lower() if args else "all"

    print("=" * 50)
    print("SYNTHCAST — GO LIVE")
    print("=" * 50)

    missing = []
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if not os.getenv("ELEVENLABS_API_KEY"):
        missing.append("ELEVENLABS_API_KEY")
    if not os.getenv("ELEVENLABS_VOICE_ID"):
        missing.append("ELEVENLABS_VOICE_ID")

    if missing:
        print(f"⚠️  Missing env vars: {', '.join(missing)}")
        print("Add them to your .env file")
        return

    tasks = []

    if platform in ("all", "youtube"):
        video_id = os.getenv("YOUTUBE_VIDEO_ID")
        if not video_id:
            print("⚠️  YOUTUBE_VIDEO_ID not set — skipping YouTube")
        else:
            print(f"✅ YouTube: video {video_id}")
            tasks.append(run_youtube())

    if platform in ("all", "twitch"):
        channel = os.getenv("TWITCH_CHANNEL")
        token = os.getenv("TWITCH_TOKEN")
        if not channel or not token:
            print("⚠️  TWITCH_CHANNEL or TWITCH_TOKEN not set — skipping Twitch")
        else:
            print(f"✅ Twitch: #{channel}")
            tasks.append(run_twitch())

    if not tasks:
        print("❌ No platforms configured. Check your .env file.")
        return

    print(f"\n🚀 Going live on {len(tasks)} platform(s)...")
    print("Press Ctrl+C to stop all\n")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n[Synthcast] Stopped all listeners.")


if __name__ == "__main__":
    asyncio.run(main())
