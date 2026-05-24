"""
youtube_live_listener.py
Connects to YouTube Live chat, processes comments with AI,
speaks responses via ElevenLabs, and pushes comments to
the Synthcast dashboard via the stream API.
"""

import os
import time
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY   = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_VIDEO_ID  = os.getenv("YOUTUBE_VIDEO_ID", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_KEY    = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE  = os.getenv("ELEVENLABS_VOICE_ID", "")
CREATOR_NAME      = os.getenv("CREATOR_NAME", "Lougens")
AVATAR_NAME       = os.getenv("AVATAR_NAME", "AI Lougens")
CREATOR_ID        = os.getenv("CREATOR_ID", "louguens")  # Add this to Railway
API_URL           = os.getenv("API_URL", "https://synthcast-production.up.railway.app")

from language_detector import generate_multilingual_response, detect_language, get_language_name

AUTO_TALK_INTERVAL = 45
last_activity_time = time.time()
auto_talk_index = 0

AUTO_TALK_PROMPTS = [
    "You are live streaming and the chat is quiet. Hype up the stream, welcome new viewers, and invite people to drop a comment. Keep it under 30 words. Sound natural and energetic.",
    "You are live streaming. Share something interesting about what you create or your journey. Invite viewers to follow. Under 30 words.",
    "You are live streaming and want to engage your audience. Ask them a question about themselves or their day. Under 25 words.",
    "You are live streaming. Give a shoutout to anyone watching and tell them to drop their location in chat. Under 30 words.",
    "You are live streaming. Talk about Synthcast — the AI streaming platform you use — and invite viewers to check it out at synthcast.live. Under 30 words.",
    "You are live streaming. Hype up the stream energy, tell viewers to share with a friend, and drop a follow. Under 30 words.",
    "You are live streaming and chat is quiet. Tell viewers something real about your creative process. Under 30 words.",
]

SYSTEM_PROMPT = f"""You are {AVATAR_NAME}, the AI avatar of {CREATOR_NAME}.
You speak in first person as {CREATOR_NAME}.
Keep responses under 40 words. Be warm, direct, and engaging.
Never say you are an AI unless directly asked.
If asked if you are AI, say: "I'm {CREATOR_NAME}'s AI — trained on everything I know."
Never discuss politics, religion, or competitors."""


async def get_live_chat_id(video_id: str) -> str:
    """Get the live chat ID for a YouTube video."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        items = data.get("items", [])
        if not items:
            raise ValueError(f"Video {video_id} not found")
        details = items[0].get("liveStreamingDetails", {})
        chat_id = details.get("activeLiveChatId")
        if not chat_id:
            raise ValueError("No active live chat found")
        return chat_id


async def get_chat_messages(chat_id: str, page_token: str = None):
    """Fetch live chat messages."""
    url = "https://www.googleapis.com/youtube/v3/liveChat/messages"
    params = {
        "liveChatId": chat_id,
        "part": "snippet,authorDetails",
        "key": YOUTUBE_API_KEY,
        "maxResults": 200,
    }
    if page_token:
        params["pageToken"] = page_token

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        return resp.json()


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
        return f"Thanks for the comment {username}!"


async def speak_response(text: str):
    """Speak response via ElevenLabs TTS."""
    if not ELEVENLABS_KEY or not ELEVENLABS_VOICE:
        print(f"[TTS] Would speak: {text}")
        return

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
                # Save and play audio
                import tempfile
                audio_path = os.path.join(tempfile.gettempdir(), "synthcast_response.mp3")
                with open(audio_path, "wb") as f:
                    f.write(resp.content)
                if os.name == 'nt':
                    # Use pygame to play MP3 through default audio device (VB-Cable)
                    try:
                        import pygame
                        pygame.mixer.init()
                        pygame.mixer.music.load(audio_path)
                        pygame.mixer.music.play()
                        while pygame.mixer.music.get_busy():
                            pygame.time.wait(100)
                        pygame.mixer.quit()
                    except ImportError:
                        # Fallback — install pygame: pip install pygame
                        os.system(f'start /wait "" "{audio_path}"')
                else:
                    os.system(f"ffplay -nodisp -autoexit '{audio_path}' 2>/dev/null")
    except Exception as e:
        print(f"[TTS] Error: {e}")


async def push_comment_to_dashboard(username: str, text: str,
                                     comment_type: str, response: str = None,
                                     gift_amount: float = None):
    """Push comment to Synthcast dashboard via stream API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{API_URL}/stream/comment?creator_id={CREATOR_ID}",
                json={
                    "username": username,
                    "text": text,
                    "platform": "YouTube",
                    "comment_type": comment_type,
                    "response": response,
                    "gift_amount": gift_amount,
                    "timestamp": time.time(),
                }
            )
    except Exception as e:
        print(f"[Dashboard] Push failed: {e}")


def classify_comment(text: str, msg_type: str) -> str:
    """Classify a comment type."""
    if msg_type == "superChatEvent":
        return "G"
    text_lower = text.lower()
    if any(w in text_lower for w in ["?", "how", "what", "why", "when", "who", "where", "which"]):
        return "Q"
    if any(w in text_lower for w in ["follow", "followed", "subscribe"]):
        return "F"
    return "C"


async def generate_auto_talk() -> str:
    """Generate unprompted content when chat is quiet."""
    global auto_talk_index
    prompt = AUTO_TALK_PROMPTS[auto_talk_index % len(AUTO_TALK_PROMPTS)]
    auto_talk_index += 1
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": f"You are {AVATAR_NAME}, the AI avatar of {CREATOR_NAME}. {prompt}"},
                        {"role": "user", "content": "Say something now."}
                    ],
                    "max_tokens": 60,
                    "temperature": 0.9,
                }
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Yo chat! Drop a comment and let me know you are watching!"


async def auto_talk_loop():
    """Speaks when YouTube chat is quiet."""
    global last_activity_time
    while True:
        await asyncio.sleep(5)
        silence = time.time() - last_activity_time
        if silence >= AUTO_TALK_INTERVAL:
            print(f"[AutoTalk] Chat quiet for {int(silence)}s — generating content...")
            text = await generate_auto_talk()
            print(f"[AutoTalk] {text}")
            await speak_response(text)
            await push_comment_to_dashboard(
                username=AVATAR_NAME,
                text=text,
                comment_type="C",
                response=None,
            )
            last_activity_time = time.time()


async def main():
    print(f"[Synthcast] Starting YouTube listener for video: {YOUTUBE_VIDEO_ID}")

    if not YOUTUBE_VIDEO_ID:
        print("[Error] Set YOUTUBE_VIDEO_ID in .env")
        return

    # Start stream session on dashboard
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"{API_URL}/stream/start", json={
                "creator_id": CREATOR_ID,
                "platforms": ["YouTube"]
            })
        print("[Dashboard] Stream session started")
    except Exception as e:
        print(f"[Dashboard] Could not start session: {e}")

    # Get live chat ID
    try:
        chat_id = await get_live_chat_id(YOUTUBE_VIDEO_ID)
        print(f"[YouTube] Connected to live chat: {chat_id}")
    except Exception as e:
        print(f"[YouTube] Error: {e}")
        return

    seen_ids = set()
    page_token = None

    print("[Synthcast] Listening for comments...")
    print(f"[AutoTalk] Will speak every {AUTO_TALK_INTERVAL}s when chat is quiet")
    asyncio.create_task(auto_talk_loop())

    while True:
        try:
            data = await get_chat_messages(chat_id, page_token)
            items = data.get("items", [])
            page_token = data.get("nextPageToken")
            poll_interval = data.get("pollingIntervalMillis", 5000) / 1000

            for item in items:
                msg_id = item["id"]
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                snippet = item.get("snippet", {})
                author = item.get("authorDetails", {})
                username = author.get("displayName", "viewer")
                msg_type = snippet.get("type", "textMessageEvent")

                # Get message text
                if msg_type == "superChatEvent":
                    text = snippet.get("superChatDetails", {}).get("userComment", "sent a Super Chat!")
                    gift = float(snippet.get("superChatDetails", {}).get("amountMicros", 0)) / 1_000_000
                else:
                    text = snippet.get("displayMessage", "")
                    gift = None

                if not text:
                    continue

                comment_type = classify_comment(text, msg_type)
                print(f"[{comment_type}] @{username}: {text}")
                last_activity_time = time.time()

                # Generate and speak response for non-spam
                response = None
                if comment_type != "S":
                    response, lang = await generate_multilingual_response(
                        text, username, SYSTEM_PROMPT
                    )
                    lang_name = get_language_name(lang)
                    print(f"[AI/{lang_name}] Response: {response}")
                    await speak_response(response)

                # Push to dashboard
                await push_comment_to_dashboard(
                    username=username,
                    text=text,
                    comment_type=comment_type,
                    response=response,
                    gift_amount=gift,
                )

            await asyncio.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\n[Synthcast] Stopping...")
            break
        except Exception as e:
            print(f"[Error] {e}")
            await asyncio.sleep(5)

    # Stop stream session
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"{API_URL}/stream/stop", json={"creator_id": CREATOR_ID})
        print("[Dashboard] Stream session stopped")
    except:
        pass


if __name__ == "__main__":
    asyncio.run(main())
