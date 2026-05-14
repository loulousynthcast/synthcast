"""
test_game_commentary.py

Tests the full game commentary pipeline:
  1. Captures your screen
  2. GPT-4o reads the game state
  3. Generates commentary in your voice
  4. ElevenLabs speaks it out loud

HOW TO USE:
  1. Open any game on your screen (Fortnite, FIFA, Minecraft, anything)
  2. Run: python test_game_commentary.py
  3. Hear your AI avatar comment on your gameplay
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, ".")

from game.commentary import GameCommentaryEngine, ScreenCapture, GameStateReader, CommentaryGenerator
from stream.tts import get_tts_engine


async def main():
    print("=" * 55)
    print("SYNTHCAST GAME COMMENTARY TEST")
    print("=" * 55)
    print()

    # Check dependencies
    try:
        import mss
        from PIL import Image
        print("Screen capture: OK")
    except ImportError:
        print("Installing screen capture dependencies...")
        os.system("pip install mss pillow")
        import mss
        from PIL import Image
        print("Screen capture: OK")

    # Setup
    screen   = ScreenCapture()
    reader   = GameStateReader()
    gen      = CommentaryGenerator(
        creator_name = os.getenv("CREATOR_NAME", "Louguens"),
        personality  = os.getenv("CREATOR_PERSONALITY", "confident, energetic, entertaining"),
    )
    tts = get_tts_engine()

    print("TTS engine:", "Ready" if not tts.simulation_mode else "Simulation mode")
    print()
    print("Open your game now. Capturing screen in 3 seconds...")
    await asyncio.sleep(3)

    # ── TEST 1: Read game state ──────────────────────────────
    print()
    print("Step 1 — Reading your screen...")
    try:
        screenshot_b64 = screen.capture_b64()
        print("Screenshot captured OK")

        game_state = await reader.read(screenshot_b64)
        print("Game state detected:")
        print("  " + game_state)

    except Exception as e:
        print("Screen capture failed:", e)
        return

    # ── TEST 2: Generate commentary ──────────────────────────
    print()
    print("Step 2 — Generating commentary...")
    commentary = await gen.generate(game_state)

    if commentary:
        print("Commentary generated:")
        print("  " + commentary)
        print()
        print("Step 3 — Speaking in your voice...")
        await tts.speak(commentary, play=True)
        print("Done!")
    else:
        print("Nothing interesting to comment on.")
        print("Try opening a more active game scene.")

    # ── TEST 3: Viewer question ──────────────────────────────
    print()
    print("Step 4 — Testing viewer question...")
    print('Simulating: @superfan99 asks "What\'s happening in the game?"')

    engine = GameCommentaryEngine(
        creator_name=os.getenv("CREATOR_NAME", "Louguens"),
        personality=os.getenv("CREATOR_PERSONALITY", "confident, energetic, entertaining"),
        tts_engine=tts,
    )
    engine._current_state = game_state

    answer = await engine.handle_game_question(
        "superfan99",
        "What's happening in the game right now?"
    )

    if answer:
        print("Answer generated:")
        print("  " + answer)
        print("Speaking...")
        await tts.speak(answer, play=True)
        print("Done!")

    # ── TEST 4: Auto-commentary for 60 seconds ───────────────
    print()
    response = input("Run 60 seconds of auto-commentary? (y/n): ").strip().lower()
    if response == "y":
        print()
        print("Running auto-commentary for 60 seconds...")
        print("Watch your game — your avatar will comment on interesting moments.")
        print("Press Ctrl+C to stop early.")
        print()

        engine2 = GameCommentaryEngine(
            creator_name=os.getenv("CREATOR_NAME", "Louguens"),
            personality=os.getenv("CREATOR_PERSONALITY", "confident, energetic, entertaining"),
            commentary_interval_s=15.0,
            tts_engine=tts,
        )

        try:
            await asyncio.wait_for(engine2.run_auto_commentary(), timeout=60)
        except asyncio.TimeoutError:
            engine2.stop()
            print()
            print("60 seconds complete!")
            print("Stats:", engine2.stats())

    print()
    print("=" * 55)
    print("Game commentary test complete!")
    print("To use live: run stream/orchestrator.py with GAME_COMMENTARY=true")
    print("=" * 55)


asyncio.run(main())
