"""
synthcast/game/commentary.py

Level 1 Game Commentary Module.
Captures your screen, reads the game state using GPT-4o Vision,
and generates live commentary that your avatar speaks on stream.

How it works:
  1. Captures your screen every N seconds
  2. Sends screenshot to GPT-4o Vision
  3. GPT-4o describes what it sees in the game
  4. Agent generates commentary in your voice
  5. ElevenLabs speaks it on your live stream

Also handles viewer questions about the game:
  Viewer: "how many kills do you have?"
  Avatar: reads screen → "Just hit 8 kills, we're top 3 right now!"

Works with ANY game — no game-specific setup needed.

Install:
    pip install pillow openai mss

YOUR PART:
  Just run it while streaming any game.
  No configuration needed beyond your existing .env
"""

import os
import asyncio
import base64
import time
import io
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


# ── SCREEN CAPTURE ────────────────────────────────────────────────────────────

class ScreenCapture:
    """
    Captures your screen (or a specific window/region).
    Uses MSS for fast, cross-platform screen capture.
    """

    def __init__(self, region: Optional[dict] = None):
        """
        region: {"top": 0, "left": 0, "width": 1920, "height": 1080}
        None = full screen
        """
        self.region = region

    def capture(self) -> bytes:
        """Capture screen and return as JPEG bytes."""
        try:
            import mss
            import mss.tools
            from PIL import Image

            with mss.mss() as sct:
                monitor = self.region or sct.monitors[1]
                screenshot = sct.grab(monitor)

                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

                # Resize to reduce API costs (720p is plenty for game reading)
                img.thumbnail((1280, 720), Image.LANCZOS)

                # Convert to JPEG bytes
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=75)
                return buf.getvalue()

        except ImportError:
            raise ImportError("Run: pip install mss pillow")

    def capture_b64(self) -> str:
        """Capture screen and return as base64 string for API calls."""
        return base64.b64encode(self.capture()).decode("utf-8")


# ── GAME STATE READER ─────────────────────────────────────────────────────────

class GameStateReader:
    """
    Uses GPT-4o Vision to read the game state from a screenshot.
    Returns a structured description of what's happening in the game.
    """

    SYSTEM_PROMPT = """You are a game analyst watching a live stream.
Your job is to describe what you see in the game screenshot concisely.

Focus on:
- What game is being played
- Current game state (health, score, kills, position, etc.)
- What just happened or is happening right now
- Any notable moments (close call, good play, death, victory, etc.)

Be specific and accurate. Use gaming terminology.
Keep your description under 60 words.
If you can't identify the game or see no game, say "no game visible"."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")

    async def read(self, screenshot_b64: str) -> str:
        """Send screenshot to GPT-4o Vision and get game state description."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        client = AsyncOpenAI(api_key=self.api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=150,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_b64}",
                                "detail": "low"  # cheaper, faster, enough for game reading
                            }
                        },
                        {"type": "text", "text": "What's happening in this game right now?"}
                    ]
                }
            ]
        )
        return response.choices[0].message.content.strip()

    async def answer_question(self, screenshot_b64: str, question: str) -> str:
        """Answer a viewer's specific question about the game using the screenshot."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        client = AsyncOpenAI(api_key=self.api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=100,
            messages=[
                {
                    "role": "system",
                    "content": "You are analyzing a game screenshot to answer a viewer's question. Be accurate and concise. Under 40 words."
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{screenshot_b64}",
                                "detail": "low"
                            }
                        },
                        {"type": "text", "text": f"Viewer asks: {question}\nAnswer based on what you see in the screenshot."}
                    ]
                }
            ]
        )
        return response.choices[0].message.content.strip()


# ── COMMENTARY GENERATOR ──────────────────────────────────────────────────────

class CommentaryGenerator:
    """
    Takes game state descriptions and generates
    natural spoken commentary in the creator's voice.
    """

    def __init__(
        self,
        creator_name: str = "Creator",
        personality: str = "confident, energetic, entertaining",
        api_key: Optional[str] = None,
    ):
        self.creator_name = creator_name
        self.personality = personality
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._last_commentary: Optional[str] = None
        self._last_game_state: Optional[str] = None

    async def generate(self, game_state: str) -> Optional[str]:
        """
        Generate commentary for the current game state.
        Returns None if nothing interesting to say.
        """
        # Don't comment if nothing changed significantly
        if game_state == self._last_game_state:
            return None
        if "no game visible" in game_state.lower():
            return None

        self._last_game_state = game_state

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        client = AsyncOpenAI(api_key=self.api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=80,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are {self.creator_name}'s AI avatar doing live game commentary.
Personality: {self.personality}
Rules:
- Speak naturally like a streamer, not a robot
- React to what's happening — excitement, tension, humor
- Keep it under 30 words
- Don't repeat yourself
- If nothing exciting is happening, return exactly: SKIP
Last thing you said: {self._last_commentary or 'nothing yet'}"""
                },
                {
                    "role": "user",
                    "content": f"Game state: {game_state}\nGenerate a natural streamer reaction or commentary. If nothing new to say, return SKIP."
                }
            ]
        )

        text = response.choices[0].message.content.strip()

        if text == "SKIP" or not text:
            return None

        self._last_commentary = text
        return text

    async def react_to_question(
        self,
        game_state: str,
        username: str,
        question: str,
        game_answer: str,
    ) -> str:
        """Generate a natural response to a viewer's game question."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        client = AsyncOpenAI(api_key=self.api_key)

        response = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=80,
            messages=[
                {
                    "role": "system",
                    "content": f"""You are {self.creator_name}'s AI avatar.
Personality: {self.personality}
A viewer asked a question about your gameplay. Answer naturally, mentioning their username.
Keep it under 35 words."""
                },
                {
                    "role": "user",
                    "content": f"Viewer @{username} asked: '{question}'\nGame answer from screenshot: {game_answer}\nCurrent game state: {game_state}\nRespond naturally:"
                }
            ]
        )

        return response.choices[0].message.content.strip()


# ── GAME COMMENTARY ENGINE ────────────────────────────────────────────────────

class GameCommentaryEngine:
    """
    Main engine that ties everything together.
    Runs alongside the regular stream agent.

    Two modes:
    1. Auto-commentary: reads screen every N seconds, speaks interesting moments
    2. Question mode: viewer asks about the game, avatar reads screen and answers
    """

    GAME_KEYWORDS = [
        "what game", "how many kills", "what's your score", "what level",
        "how much health", "how many kills", "what map", "what rank",
        "are you winning", "how many deaths", "what's happening",
        "what did you just do", "how did you do that", "what weapon",
        "what character", "what team", "what's the score",
    ]

    def __init__(
        self,
        creator_name: str = "Creator",
        personality: str = "confident, energetic, entertaining",
        commentary_interval_s: float = 30.0,
        agent_api_url: str = "http://localhost:8000",
        tts_engine=None,
    ):
        self.creator_name    = creator_name
        self.personality     = personality
        self.interval        = commentary_interval_s
        self.agent_url       = agent_api_url
        self.tts             = tts_engine

        self.screen          = ScreenCapture()
        self.reader          = GameStateReader()
        self.generator       = CommentaryGenerator(creator_name, personality)

        self._running        = False
        self._current_state  = ""
        self._commentary_count = 0
        self._question_count   = 0

    def is_game_question(self, comment_text: str) -> bool:
        """Check if a viewer comment is asking about the game."""
        text_lower = comment_text.lower()
        return any(kw in text_lower for kw in self.GAME_KEYWORDS)

    async def handle_game_question(self, username: str, question: str) -> Optional[str]:
        """
        Called when a viewer asks a game question.
        Reads the screen and generates a specific answer.
        """
        try:
            screenshot_b64 = self.screen.capture_b64()
            game_answer = await self.reader.answer_question(screenshot_b64, question)
            response = await self.generator.react_to_question(
                self._current_state, username, question, game_answer
            )
            self._question_count += 1
            return response
        except Exception as e:
            print(f"[GameCommentary] Question handling error: {e}")
            return None

    async def run_auto_commentary(self):
        """
        Runs in background — reads screen every N seconds
        and generates commentary for interesting moments.
        """
        self._running = True
        print(f"[GameCommentary] Auto-commentary started (every {self.interval}s)")

        while self._running:
            try:
                await asyncio.sleep(self.interval)

                if not self._running:
                    break

                # Capture and read screen
                screenshot_b64 = self.screen.capture_b64()
                game_state = await self.reader.read(screenshot_b64)
                self._current_state = game_state

                print(f"[GameCommentary] Game state: {game_state[:80]}...")

                # Generate commentary
                commentary = await self.generator.generate(game_state)

                if commentary:
                    print(f"[GameCommentary] Speaking: {commentary}")
                    self._commentary_count += 1

                    # Speak via TTS
                    if self.tts:
                        await self.tts.speak(commentary, play=True)
                    else:
                        print(f"[GameCommentary] (TTS not configured) Would say: {commentary}")

            except Exception as e:
                print(f"[GameCommentary] Error: {e}")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
        print(f"[GameCommentary] Stopped. {self._commentary_count} comments, {self._question_count} questions answered.")

    def stats(self) -> dict:
        return {
            "running":           self._running,
            "commentary_count":  self._commentary_count,
            "question_count":    self._question_count,
            "current_game":      self._current_state[:50] if self._current_state else "unknown",
            "interval_s":        self.interval,
        }


# ── INTEGRATED STREAM LISTENER ────────────────────────────────────────────────

class GameAwareListener:
    """
    Wraps any platform listener and adds game awareness.
    Game questions go to the GameCommentaryEngine.
    Other comments go to the regular agent.

    Drop-in replacement for any platform listener.
    """

    def __init__(
        self,
        platform_listener,
        game_engine: GameCommentaryEngine,
        tts_engine=None,
    ):
        self.listener    = platform_listener
        self.game_engine = game_engine
        self.tts         = tts_engine

    async def start(self):
        """Run game commentary + platform listener simultaneously."""
        await asyncio.gather(
            self.listener.start(),
            self.game_engine.run_auto_commentary(),
        )

    def stop(self):
        self.listener.stop()
        self.game_engine.stop()


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_game_engine(
    tts_engine=None,
    commentary_interval_s: float = 30.0,
) -> GameCommentaryEngine:
    """
    Factory. Creates a game commentary engine from .env config.

    commentary_interval_s: how often to read the screen and comment.
      30s = comment roughly every 30 seconds on interesting moments
      60s = less frequent, less API cost
      15s = very active commentary, higher cost
    """
    return GameCommentaryEngine(
        creator_name          = os.getenv("CREATOR_NAME", "Creator"),
        personality           = os.getenv("CREATOR_PERSONALITY", "confident, energetic, entertaining"),
        commentary_interval_s = commentary_interval_s,
        agent_api_url         = os.getenv("SYNTHCAST_API_URL", "http://localhost:8000"),
        tts_engine            = tts_engine,
    )


# ── STANDALONE TEST ───────────────────────────────────────────────────────────

async def test_commentary():
    """
    Test the game commentary engine.
    Open any game on your screen, then run this.

    python -m game.commentary
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from stream.tts import get_tts_engine

    print("Synthcast Game Commentary — Test Mode")
    print("Open any game on your screen now.")
    print("-" * 50)

    tts = get_tts_engine()
    engine = get_game_engine(tts_engine=tts, commentary_interval_s=15.0)

    print("Capturing screen in 3 seconds...")
    await asyncio.sleep(3)

    # Single test capture
    try:
        screenshot_b64 = engine.screen.capture_b64()
        print("Screen captured OK")

        game_state = await engine.reader.read(screenshot_b64)
        print(f"Game state: {game_state}")

        commentary = await engine.generator.generate(game_state)
        if commentary:
            print(f"Commentary: {commentary}")
            print("Speaking...")
            await tts.speak(commentary, play=True)
            print("Done!")
        else:
            print("Nothing interesting to comment on right now.")

        # Test a viewer question
        print("\nTesting viewer question...")
        answer = await engine.handle_game_question(
            "superfan99",
            "What's happening in the game right now?"
        )
        if answer:
            print(f"Answer: {answer}")
            await tts.speak(answer, play=True)

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure you have a game open on your screen.")


if __name__ == "__main__":
    asyncio.run(test_commentary())
