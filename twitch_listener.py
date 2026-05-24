"""
twitch_listener.py
Connects to Twitch IRC chat, processes comments with AI,
speaks responses via ElevenLabs, and pushes to Synthcast dashboard.

Run alongside youtube_live_listener.py for simultaneous multi-platform streaming.
"""

import os
import time
import asyncio
import socket
import httpx
from dotenv import load_dotenv

load_dotenv()

TWITCH_TOKEN    = os.getenv("TWITCH_TOKEN", "")       # oauth:xxxx
TWITCH_CHANNEL  = os.getenv("TWITCH_CHANNEL", "")     # your channel name
TWITCH_USERNAME = os.getenv("TWITCH_USERNAME", os.getenv("TWITCH_CHANNEL", ""))
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "")
CREATOR_NAME    = os.getenv("CREATOR_NAME", "Lougens")
AVATAR_NAME     = os.getenv("AVATAR_NAME", "AI Lougens")
CREATOR_ID      = os.getenv("CREATOR_ID", "louguens")
API_URL         = os.getenv("API_URL", "https://synthcast-production.up.railway.app")

TWITCH_IRC_HOST = "irc.chat.twitch.tv"
TWITCH_IRC_PORT = 6667

# Auto-talk settings
AUTO_TALK_INTERVAL = 45  # seconds of silence before AI speaks
last_activity_time = time.time()

# Auto-talk prompts — AI generates fresh content each time
AUTO_TALK_PROMPTS = [
    "You are live streaming and the chat is quiet. Hype up the stream, welcome new viewers, and invite people to drop a comment. Keep it under 30 words. Sound natural and energetic.",
    "You are live streaming. Share something interesting about what you create or your journey. Invite viewers to follow. Under 30 words.",
    "You are live streaming and want to engage your audience. Ask them a question about themselves or their day. Under 25 words.",
    "You are live streaming. Give a shoutout to anyone watching and tell them to drop their location in chat. Under 30 words.",
    "You are live streaming. Talk about Synthcast — the AI streaming platform you use — and invite viewers to check it out at synthcast.live. Under 30 words.",
    "You are live streaming. Hype up the stream energy, tell viewers to share the stream with a friend, and drop a follow if they haven't. Under 30 words.",
    "You are live streaming and chat is quiet. Tell viewers something real about your creative process or what you are working on right now. Under 30 words.",
]

auto_talk_index = 0

from language_detector import generate_multilingual_response, get_language_name

SYSTEM_PROMPT = f"""You are {AVATAR_NAME}, the AI avatar of {CREATOR_NAME}.
You speak in first person as {CREATOR_NAME}.
Keep responses under 40 words. Be warm, direct, and engaging.
Never say you are an AI unless directly asked.
If asked if you are AI, say: "I'm {CREATOR_NAME}'s AI — trained on everything I know."
Never discuss politics, religion, or competitors."""

# Response cooldown — don't respond to every single message
RESPONSE_COOLDOWN = 8  # seconds between responses
last_response_time = 0

# Skip these bot usernames
BOT_NAMES = {"nightbot", "streamelements", "moobot", "fossabot", "streamlabs", "botisimo"}


class TwitchIRC:
    def __init__(self):
        self.sock = None
        self.connected = False

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((TWITCH_IRC_HOST, TWITCH_IRC_PORT))
        self.sock.setblocking(False)

        # Authenticate
        self.send(f"PASS {TWITCH_TOKEN}")
        self.send(f"NICK {TWITCH_USERNAME}")
        self.send(f"JOIN #{TWITCH_CHANNEL}")
        self.connected = True
        print(f"[Twitch] Connected to #{TWITCH_CHANNEL}")

    def send(self, msg: str):
        self.sock.send(f"{msg}\r\n".encode("utf-8"))

    def send_chat(self, message: str):
        self.send(f"PRIVMSG #{TWITCH_CHANNEL} :{message}")

    def recv(self) -> list:
        try:
            data = self.sock.recv(4096).decode("utf-8", errors="ignore")
            return data.strip().split("\r\n")
        except BlockingIOError:
            return []
        except Exception as e:
            print(f"[Twitch] Recv error: {e}")
            return []

    def pong(self, server: str):
        self.send(f"PONG :{server}")


def parse_message(raw: str) -> tuple:
    """Parse a Twitch IRC message. Returns (username, message) or (None, None)."""
    try:
        if "PRIVMSG" not in raw:
            return None, None
        parts = raw.split("PRIVMSG", 1)
        username = parts[0].split("!")[0].lstrip(":")
        message = parts[1].split(":", 1)[1].strip()
        return username.lower(), message
    except Exception:
        return None, None


def classify_comment(text: str, username: str) -> str:
    """Classify comment type."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["?", "how", "what", "why", "when", "who", "where"]):
        return "Q"
    if any(w in text_lower for w in ["follow", "followed", "sub", "subscribed"]):
        return "F"
    return "C"


async def generate_response(comment: str, username: str) -> str:
    """Generate AI response via OpenAI."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"@{username} says: {comment}"}
                    ],
                    "max_tokens": 80,
                    "temperature": 0.8,
                }
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI] Error: {e}")
        return f"Thanks {username}!"


async def play_audio(audio_path: str):
    """Play audio through default output (VB-Cable → OBS)."""
    if os.name == 'nt':
        import pygame
        pygame.mixer.init()
        pygame.mixer.music.load(audio_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(100)
        pygame.mixer.quit()
    else:
        os.system(f"ffplay -nodisp -autoexit '{audio_path}' 2>/dev/null")


async def speak_response(text: str):
    """Speak response — ElevenLabs primary, Edge TTS fallback."""
    import tempfile
    audio_path = os.path.join(tempfile.gettempdir(), "synthcast_twitch.mp3")

    if ELEVENLABS_KEY and ELEVENLABS_VOICE:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}",
                    headers={"xi-api-key": ELEVENLABS_KEY},
                    json={
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
                    }
                )
                if resp.status_code == 200:
                    with open(audio_path, "wb") as f:
                        f.write(resp.content)
                    await play_audio(audio_path)
                    return
                else:
                    print(f"[TTS] ElevenLabs error {resp.status_code} — falling back to Edge TTS")
        except Exception as e:
            print(f"[TTS] ElevenLabs failed: {e} — falling back to Edge TTS")

    try:
        import edge_tts
        voice = "en-US-GuyNeural"
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(audio_path)
        print(f"[TTS/Edge] Speaking: {text[:50]}...")
        await play_audio(audio_path)
    except Exception as e:
        print(f"[TTS] Edge TTS failed: {e}")


async def push_to_dashboard(username: str, text: str, comment_type: str,
                             response: str = None):
    """Push comment to Synthcast dashboard."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{API_URL}/stream/comment?creator_id={CREATOR_ID}",
                json={
                    "username": username,
                    "text": text,
                    "platform": "Twitch",
                    "comment_type": comment_type,
                    "response": response,
                    "timestamp": time.time(),
                }
            )
    except Exception as e:
        print(f"[Dashboard] Push failed: {e}")


async def generate_auto_talk(system_prompt: str) -> str:
    """Generate an unprompted statement for when chat is quiet."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": f"You are {AVATAR_NAME}, the AI avatar of {CREATOR_NAME}. {system_prompt}"},
                        {"role": "user", "content": "Say something now."}
                    ],
                    "max_tokens": 60,
                    "temperature": 0.9,
                }
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AutoTalk] Error: {e}")
        return f"Yo chat! {CREATOR_NAME} here — drop a comment and let me know you're watching!"


async def auto_talk_loop(irc):
    """Runs in background — speaks when chat is quiet."""
    global last_activity_time, auto_talk_index
    while True:
        await asyncio.sleep(5)
        silence = time.time() - last_activity_time
        if silence >= AUTO_TALK_INTERVAL:
            prompt = AUTO_TALK_PROMPTS[auto_talk_index % len(AUTO_TALK_PROMPTS)]
            auto_talk_index += 1
            print(f"[AutoTalk] Chat quiet for {int(silence)}s — generating content...")
            text = await generate_auto_talk(prompt)
            print(f"[AutoTalk] {text}")
            # Post to chat and speak
            try:
                irc.send_chat(text)
            except:
                pass
            await speak_response(text)
            last_activity_time = time.time()  # Reset timer


async def main():
    global last_response_time

    if not TWITCH_TOKEN or not TWITCH_CHANNEL:
        print("[Error] Set TWITCH_TOKEN and TWITCH_CHANNEL in .env")
        return

    print(f"[Synthcast] Starting Twitch listener for #{TWITCH_CHANNEL}")

    # Register stream session
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"{API_URL}/stream/start", json={
                "creator_id": CREATOR_ID,
                "platforms": ["Twitch"]
            })
        print("[Dashboard] Twitch session started")
    except Exception as e:
        print(f"[Dashboard] Could not start session: {e}")

    irc = TwitchIRC()
    try:
        irc.connect()
    except Exception as e:
        print(f"[Twitch] Connection failed: {e}")
        return

    print(f"[Synthcast] Listening to #{TWITCH_CHANNEL} chat...")
    print(f"[AutoTalk] Will speak every {AUTO_TALK_INTERVAL}s when chat is quiet")

    # Start auto-talk in background
    asyncio.create_task(auto_talk_loop(irc))

    while True:
        try:
            messages = irc.recv()
            for raw in messages:
                if not raw:
                    continue

                # Handle PING
                if raw.startswith("PING"):
                    server = raw.split(":", 1)[1] if ":" in raw else "tmi.twitch.tv"
                    irc.pong(server)
                    continue

                username, text = parse_message(raw)
                if not username or not text:
                    continue

                # Skip bots
                if username in BOT_NAMES:
                    continue

                comment_type = classify_comment(text, username)
                print(f"[{comment_type}] @{username}: {text}")
                last_activity_time = time.time()  # Reset auto-talk timer

                # Cooldown check
                now = time.time()
                should_respond = (now - last_response_time) > RESPONSE_COOLDOWN

                response = None
                if should_respond and comment_type != "S":
                    response, lang = await generate_multilingual_response(
                        text, username, SYSTEM_PROMPT
                    )
                    lang_name = get_language_name(lang)
                    print(f"[AI/{lang_name}] Response: {response}")
                    last_response_time = now
                    await asyncio.gather(
                        speak_response(response),
                        asyncio.to_thread(irc.send_chat, f"@{username} {response}")
                    )

                await push_to_dashboard(username, text, comment_type, response)

            await asyncio.sleep(0.1)

        except KeyboardInterrupt:
            print("\n[Synthcast] Stopping Twitch listener...")
            break
        except Exception as e:
            print(f"[Error] {e}")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
