"""
twitch_irc_listener.py

Direct Twitch IRC connection — no client_id or client_secret needed.
Just your OAuth token and channel name.

Place this in your synthcast folder and run:
    python twitch_irc_listener.py
"""

import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()


async def main():
    token   = os.getenv("TWITCH_TOKEN", "").replace("oauth:", "")
    channel = os.getenv("TWITCH_CHANNEL", "").lower().replace("#", "")
    nick    = os.getenv("TWITCH_BOT_NICK", channel).lower()
    api_url = os.getenv("SYNTHCAST_API_URL", "http://localhost:8000")

    if not token or not channel:
        print("TWITCH_TOKEN or TWITCH_CHANNEL not set in .env")
        return

    print(f"[Twitch IRC] Connecting to #{channel} as {nick}")
    print(f"[Twitch IRC] Agent API: {api_url}")

    # Start session first
    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            await http.post(f"{api_url}/session/stop")
        except Exception:
            pass
        try:
            resp = await http.post(f"{api_url}/session/start", json={
                "brand_kit": {
                    "creator_name":  os.getenv("CREATOR_NAME", "Lougens"),
                    "avatar_name":   os.getenv("AVATAR_NAME", "AI Lougens"),
                    "personality":   os.getenv("CREATOR_PERSONALITY", "confident, warm, direct"),
                    "banned_topics": ["politics", "religion"],
                    "banned_words":  [],
                    "cta_scripts":   ["Follow for more!", "Subscribe!"],
                },
                "llm_provider":   os.getenv("LLM_PROVIDER", "openai"),
                "memory_backend": "memory",
            })
            resp.raise_for_status()
            s = resp.json()
            print(f"[Twitch IRC] Session: {s['session_id']} | Avatar: {s['avatar']}")
            print("-" * 60)
        except Exception as e:
            print(f"[Twitch IRC] Could not start session: {e}")
            print("Make sure the API server is running: uvicorn api.main:app --port 8000")
            return

    # Connect to Twitch IRC
    reader, writer = await asyncio.open_connection("irc.chat.twitch.tv", 6667)

    # Authenticate
    writer.write(f"PASS oauth:{token}\r\n".encode())
    writer.write(f"NICK {nick}\r\n".encode())
    writer.write(f"JOIN #{channel}\r\n".encode())
    await writer.drain()

    print(f"[Twitch IRC] Connected to #{channel}")
    print(f"[Twitch IRC] Type in your Twitch chat to test!")

    forwarded = 0
    errors = 0

    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=300)
                line = line.decode("utf-8", errors="ignore").strip()

                if not line:
                    continue

                # Respond to PING to keep connection alive
                if line.startswith("PING"):
                    writer.write("PONG :tmi.twitch.tv\r\n".encode())
                    await writer.drain()
                    continue

                # Parse chat message
                # Format: :username!username@username.tmi.twitch.tv PRIVMSG #channel :message
                if "PRIVMSG" in line and f"#{channel}" in line:
                    try:
                        # Extract username
                        username = line.split("!")[0].replace(":", "")

                        # Extract message
                        message = line.split(f"#{channel} :")[-1]

                        if not message or not username:
                            continue

                        # Skip bot's own messages
                        if username.lower() == nick.lower():
                            continue

                        print(f"  @{username}: {message}")

                        # Forward to agent
                        resp = await http.post(f"{api_url}/comment/process", json={
                            "platform":        "twitch",
                            "username":        username,
                            "text":            message,
                            "gift_value":      0.0,
                            "is_new_follower": False,
                            "is_subscriber":   False,
                        })

                        result = resp.json()
                        forwarded += 1

                        if result:
                            print(f"  → [{result.get('tone','?')}] {result['text']}")

                            # Speak the response
                            try:
                                import sys
                                sys.path.insert(0, ".")
                                from stream.tts import get_tts_engine
                                tts = get_tts_engine()
                                await tts.speak(result["text"], play=True)
                            except Exception as tts_err:
                                print(f"  [TTS Error] {tts_err}")
                        else:
                            print(f"  [SKIP]")

                    except Exception as parse_err:
                        errors += 1
                        if errors <= 5:
                            print(f"  [Parse error] {parse_err}")

            except asyncio.TimeoutError:
                # Send PING to keep alive
                writer.write("PING :tmi.twitch.tv\r\n".encode())
                await writer.drain()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[Twitch IRC] Error: {e}")
                await asyncio.sleep(2)

    writer.close()
    print(f"\n[Twitch IRC] Disconnected. Forwarded {forwarded} messages.")

    # Stop session
    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            resp = await http.post(f"{api_url}/session/stop")
            print("Session stats:", resp.json())
        except Exception:
            pass


asyncio.run(main())
