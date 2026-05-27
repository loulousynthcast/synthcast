"""
api/websocket_routes.py
WebSocket endpoint for real-time audio delivery to creator browsers.

Flow:
1. Creator opens synthcast.live/app in Chrome
2. Browser connects to WebSocket: wss://synthcast-production.up.railway.app/ws/{creator_id}
3. Railway generates AI response and TTS audio
4. Railway sends audio URL to browser via WebSocket
5. Browser plays audio through computer speakers → VB-Cable → OBS

Also handles:
- Stream start/stop from browser
- Real-time comment feed
- Auto-talk triggering
"""

import os
import asyncio
import time
import uuid
import httpx
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(tags=["websocket"])

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
API_URL         = os.getenv("API_URL", "https://synthcast-production.up.railway.app")

# Connected creators: creator_id -> WebSocket
connected: Dict[str, WebSocket] = {}

# Active stream sessions: creator_id -> session config
active_sessions: Dict[str, dict] = {}

# Auto-talk timers: creator_id -> last activity time
last_activity: Dict[str, float] = {}

AUTO_TALK_PROMPTS = [
    "Chat is quiet. Hype up the stream and welcome new viewers. Under 25 words.",
    "Invite viewers to drop a comment and share where they're from. Under 25 words.",
    "Share something about what you create or your journey. Under 25 words.",
    "Ask viewers a fun question to get them talking. Under 25 words.",
    "Tell viewers to follow and share the stream with a friend. Under 25 words.",
    "Talk about Synthcast — the AI platform powering your stream — and invite viewers to synthcast.live. Under 25 words.",
    "React to being live and hype the energy in chat. Under 25 words.",
]

prompt_index: Dict[str, int] = {}


async def generate_response(comment: str, username: str, system_prompt: str) -> str:
    """Generate AI response via OpenAI."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY.strip()}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"@{username} says: {comment}"}
                    ],
                    "max_tokens": 80,
                    "temperature": 0.8,
                }
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[WS] AI error: {e}")
        return f"Thanks {username}!"


async def generate_tts(text: str, voice_id: str = None) -> bytes:
    """Generate TTS audio via ElevenLabs. Returns audio bytes."""
    el_voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")
    el_key = ELEVENLABS_KEY

    if el_key and el_voice:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{el_voice}",
                    headers={"xi-api-key": el_key},
                    json={
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
                    }
                )
                if resp.status_code == 200:
                    return resp.content
                else:
                    print(f"[WS] ElevenLabs error {resp.status_code} — using Edge TTS")
        except Exception as e:
            print(f"[WS] ElevenLabs failed: {e}")

    # Edge TTS fallback
    try:
        import edge_tts
        import tempfile
        audio_path = os.path.join(tempfile.gettempdir(), f"synthcast_{uuid.uuid4().hex[:8]}.mp3")
        communicate = edge_tts.Communicate(text, "en-US-GuyNeural")
        await communicate.save(audio_path)
        with open(audio_path, "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[WS] Edge TTS failed: {e}")
        return b""


async def send_audio_to_browser(creator_id: str, text: str, audio_bytes: bytes, comment_type: str = "C"):
    """Send audio to connected browser as base64."""
    ws = connected.get(creator_id)
    if not ws or not audio_bytes:
        return
    try:
        import base64
        audio_b64 = base64.b64encode(audio_bytes).decode()
        await ws.send_json({
            "type": "audio",
            "text": text,
            "audio": audio_b64,
            "comment_type": comment_type,
            "timestamp": time.time(),
        })
    except Exception as e:
        print(f"[WS] Send audio error: {e}")


async def auto_talk_loop(creator_id: str):
    """Background task — speaks when chat is quiet."""
    session = active_sessions.get(creator_id, {})
    interval = session.get("auto_talk_interval", 45)
    system_prompt = session.get("system_prompt", f"You are an AI streaming avatar. Be engaging and natural.")
    custom_prompts = session.get("auto_talk_prompts", AUTO_TALK_PROMPTS)

    while creator_id in active_sessions and creator_id in connected:
        await asyncio.sleep(5)
        silence = time.time() - last_activity.get(creator_id, time.time())

        if silence >= interval:
            idx = prompt_index.get(creator_id, 0)
            prompt = custom_prompts[idx % len(custom_prompts)]
            prompt_index[creator_id] = idx + 1

            print(f"[AutoTalk/{creator_id}] Chat quiet {int(silence)}s — generating...")

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY.strip()}"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": f"{system_prompt}\n\n{prompt}"},
                                {"role": "user", "content": "Say something now."}
                            ],
                            "max_tokens": 60,
                            "temperature": 0.9,
                        }
                    )
                    text = resp.json()["choices"][0]["message"]["content"].strip()
            except:
                text = "Yo chat! Drop a comment and let me know you're watching!"

            print(f"[AutoTalk/{creator_id}] {text}")
            voice_id = session.get("voice_id")
            audio = await generate_tts(text, voice_id)
            await send_audio_to_browser(creator_id, text, audio, "AUTO")
            last_activity[creator_id] = time.time()


async def find_active_youtube_stream(api_key: str, channel_id: str = None) -> str:
    """Auto-detect the creator's currently live YouTube stream.
    
    Tries multiple methods:
    1. If channel_id provided, search that channel's live streams
    2. Otherwise, the creator must provide video_id
    
    Returns video_id of active live stream or empty string.
    """
    try:
        async with httpx.AsyncClient() as client:
            # If we have a channel_id, search for active live streams
            if channel_id:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "id",
                        "channelId": channel_id,
                        "eventType": "live",
                        "type": "video",
                        "key": api_key,
                    }
                )
                data = resp.json()
                items = data.get("items", [])
                if items:
                    return items[0]["id"]["videoId"]
    except Exception as e:
        print(f"[WS/YT] Auto-detect failed: {e}")
    return ""


async def youtube_listener_task(creator_id: str, api_key: str, video_id: str):
    """Listen to YouTube chat for a specific creator."""
    # Auto-detect if no video_id provided
    if not video_id:
        # Try to find the creator's active live stream
        # First, get their channel ID from the API key's account
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "id", "mine": "true", "key": api_key}
                )
                data = resp.json()
                items = data.get("items", [])
                if items:
                    channel_id = items[0]["id"]
                    video_id = await find_active_youtube_stream(api_key, channel_id)
                    if video_id:
                        print(f"[WS/YT] Auto-detected live stream: {video_id}")
        except:
            pass
        
        if not video_id:
            print(f"[WS/YT] No active live stream found for {creator_id}")
            ws = connected.get(creator_id)
            if ws:
                await ws.send_json({
                    "type": "notice",
                    "message": "No active YouTube live stream detected. Start your stream first.",
                })
            return
    
    print(f"[WS/YT] Starting listener for {creator_id}: {video_id}")
    session = active_sessions.get(creator_id, {})
    system_prompt = session.get("system_prompt", "You are an AI streaming avatar.")

    try:
        # Get live chat ID
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "liveStreamingDetails", "id": video_id, "key": api_key}
            )
            data = resp.json()
            items = data.get("items", [])
            if not items:
                print(f"[WS/YT] Video {video_id} not found")
                return
            chat_id = items[0].get("liveStreamingDetails", {}).get("activeLiveChatId")
            if not chat_id:
                print(f"[WS/YT] No active chat for {video_id}")
                return

        print(f"[WS/YT] Connected to chat: {chat_id}")
        seen_ids = set()
        page_token = None

        while creator_id in active_sessions and creator_id in connected:
            async with httpx.AsyncClient() as client:
                params = {
                    "liveChatId": chat_id,
                    "part": "snippet,authorDetails",
                    "key": api_key,
                    "maxResults": 200,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/liveChat/messages",
                    params=params
                )
                data = resp.json()
                items = data.get("items", [])
                page_token = data.get("nextPageToken")
                poll_ms = data.get("pollingIntervalMillis", 5000)

                for item in items:
                    msg_id = item["id"]
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    username = item.get("authorDetails", {}).get("displayName", "viewer")
                    text = item.get("snippet", {}).get("displayMessage", "")
                    if not text:
                        continue

                    print(f"[WS/YT] @{username}: {text}")
                    last_activity[creator_id] = time.time()

                    response = await generate_response(text, username, system_prompt)
                    voice_id = active_sessions.get(creator_id, {}).get("voice_id")
                    audio = await generate_tts(response, voice_id)
                    await send_audio_to_browser(creator_id, response, audio)

                    ws = connected.get(creator_id)
                    if ws:
                        await ws.send_json({
                            "type": "comment",
                            "username": username,
                            "text": text,
                            "response": response,
                            "platform": "YouTube",
                            "timestamp": time.time(),
                        })

            await asyncio.sleep(poll_ms / 1000)

    except Exception as e:
        print(f"[WS/YT] Error: {e}")


async def twitch_listener_task(creator_id: str, channel: str, token: str):
    """Listen to Twitch chat for a specific creator."""
    import socket as sock_module
    print(f"[WS/Twitch] Starting listener for {creator_id}: #{channel}")
    session = active_sessions.get(creator_id, {})
    system_prompt = session.get("system_prompt", "You are an AI streaming avatar.")
    last_resp = time.time()
    cooldown = 8

    try:
        s = sock_module.socket(sock_module.AF_INET, sock_module.SOCK_STREAM)
        s.connect(("irc.chat.twitch.tv", 6667))
        s.setblocking(False)

        def irc_send(msg):
            s.send(f"{msg}\r\n".encode("utf-8"))

        irc_send(f"PASS {token}")
        irc_send(f"NICK {channel}")
        irc_send(f"JOIN #{channel}")
        print(f"[WS/Twitch] Connected to #{channel}")

        while creator_id in active_sessions and creator_id in connected:
            try:
                data = s.recv(4096).decode("utf-8", errors="ignore")
                for raw in data.strip().split("\r\n"):
                    if raw.startswith("PING"):
                        irc_send(f"PONG :{raw.split(':',1)[1] if ':' in raw else 'tmi.twitch.tv'}")
                        continue
                    if "PRIVMSG" not in raw:
                        continue
                    try:
                        username = raw.split("!")[0].lstrip(":").lower()
                        text = raw.split("PRIVMSG", 1)[1].split(":", 1)[1].strip()
                    except:
                        continue

                    if not text:
                        continue

                    print(f"[WS/Twitch] @{username}: {text}")
                    last_activity[creator_id] = time.time()

                    if time.time() - last_resp < cooldown:
                        continue

                    response = await generate_response(text, username, system_prompt)
                    last_resp = time.time()
                    voice_id = active_sessions.get(creator_id, {}).get("voice_id")
                    audio = await generate_tts(response, voice_id)
                    await send_audio_to_browser(creator_id, response, audio)

                    try:
                        irc_send(f"PRIVMSG #{channel} :@{username} {response}")
                    except:
                        pass

                    ws = connected.get(creator_id)
                    if ws:
                        await ws.send_json({
                            "type": "comment",
                            "username": username,
                            "text": text,
                            "response": response,
                            "platform": "Twitch",
                            "timestamp": time.time(),
                        })

            except BlockingIOError:
                pass
            except Exception as e:
                print(f"[WS/Twitch] Error: {e}")

            await asyncio.sleep(0.1)

        s.close()

    except Exception as e:
        print(f"[WS/Twitch] Connection error: {e}")


@router.websocket("/ws/{creator_id}")
async def websocket_endpoint(websocket: WebSocket, creator_id: str):
    """Main WebSocket connection for a creator's browser."""
    await websocket.accept()
    connected[creator_id] = websocket
    last_activity[creator_id] = time.time()

    print(f"[WS] Creator connected: {creator_id}")

    # Send connection confirmation
    await websocket.send_json({
        "type": "connected",
        "creator_id": creator_id,
        "message": "Synthcast AI connected. Ready to go live.",
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "start_stream":
                # Load creator's stored credentials from database
                voice_id = data.get("voice_id", "")
                el_key = ""
                yt_api_key = ""
                yt_video_id = ""
                twitch_channel = ""
                twitch_token = ""

                try:
                    from billing.db_auth_store import get_user_by_id
                    user = get_user_by_id(creator_id)
                    if user:
                        voice_id = voice_id or user.get("elevenlabs_voice_id", "")
                        el_key = user.get("elevenlabs_api_key", "")
                        yt_api_key = user.get("youtube_api_key", "")
                        yt_video_id = user.get("youtube_video_id", data.get("youtube_video_id", ""))
                        twitch_channel = user.get("twitch_channel", "")
                        twitch_token = user.get("twitch_token", "")
                except Exception as e:
                    print(f"[WS] Could not load creator credentials: {e}")

                session_config = {
                    "platform": data.get("platform", "YouTube"),
                    "system_prompt": data.get("system_prompt", f"You are an AI streaming avatar. Be warm, engaging, and natural."),
                    "voice_id": voice_id,
                    "el_key": el_key,
                    "yt_api_key": yt_api_key,
                    "yt_video_id": yt_video_id,
                    "twitch_channel": twitch_channel,
                    "twitch_token": twitch_token,
                    "auto_talk_interval": data.get("auto_talk_interval", 45),
                    "auto_talk_prompts": data.get("auto_talk_prompts", AUTO_TALK_PROMPTS),
                    "started_at": time.time(),
                }
                active_sessions[creator_id] = session_config
                last_activity[creator_id] = time.time()

                asyncio.create_task(auto_talk_loop(creator_id))

                # Start platform listeners if credentials available
                if yt_api_key and yt_video_id:
                    asyncio.create_task(youtube_listener_task(creator_id, yt_api_key, yt_video_id))
                if twitch_channel and twitch_token:
                    asyncio.create_task(twitch_listener_task(creator_id, twitch_channel, twitch_token))

                await websocket.send_json({
                    "type": "stream_started",
                    "platforms": {
                        "youtube": bool(yt_api_key and yt_video_id),
                        "twitch": bool(twitch_channel and twitch_token),
                    },
                    "message": "Stream started. Platform listeners active.",
                })
                print(f"[WS] Stream started: {creator_id} — YT:{bool(yt_api_key)} Twitch:{bool(twitch_channel)}")

            elif msg_type == "stop_stream":
                # Creator clicked End Session
                active_sessions.pop(creator_id, None)
                await websocket.send_json({"type": "stream_stopped"})
                print(f"[WS] Stream stopped: {creator_id}")

            elif msg_type == "comment":
                # New comment from platform listener
                username = data.get("username", "viewer")
                text = data.get("text", "")
                platform = data.get("platform", "chat")

                last_activity[creator_id] = time.time()

                session = active_sessions.get(creator_id, {})
                system_prompt = session.get("system_prompt", "You are an AI streaming avatar.")

                # Generate response
                response = await generate_response(text, username, system_prompt)
                print(f"[WS] Response to @{username}: {response}")

                # Generate audio
                voice_id = session.get("voice_id")
                audio = await generate_tts(response, voice_id)

                # Send to browser for playback
                await send_audio_to_browser(creator_id, response, audio)

                # Also notify about the comment
                await websocket.send_json({
                    "type": "comment",
                    "username": username,
                    "text": text,
                    "response": response,
                    "platform": platform,
                    "timestamp": time.time(),
                })

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"[WS] Creator disconnected: {creator_id}")
    except Exception as e:
        print(f"[WS] Error: {e}")
    finally:
        connected.pop(creator_id, None)
        active_sessions.pop(creator_id, None)
        last_activity.pop(creator_id, None)


@router.get("/ws/status")
async def ws_status():
    """Get connected creators."""
    return {
        "connected": len(connected),
        "active_streams": len(active_sessions),
        "creators": list(active_sessions.keys()),
    }


def push_comment_to_creator(creator_id: str, username: str, text: str, platform: str):
    """Called by platform listeners to push comments to connected browser."""
    ws = connected.get(creator_id)
    if ws:
        asyncio.create_task(ws.send_json({
            "type": "incoming_comment",
            "username": username,
            "text": text,
            "platform": platform,
            "timestamp": time.time(),
        }))
