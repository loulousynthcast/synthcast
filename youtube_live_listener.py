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
                with open("/tmp/response.mp3", "wb") as f:
                    f.write(resp.content)
                os.system("ffplay -nodisp -autoexit /tmp/response.mp3 2>/dev/null")
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

                # Generate and speak response for non-spam
                response = None
                if comment_type != "S":
                    response = await generate_response(text, username)
                    print(f"[AI] Response: {response}")
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
