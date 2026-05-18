"""
youtube_live_listener.py
YouTube Live comment listener with TTS responses.
Run this while live on YouTube — your AI avatar speaks every response.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, '.')

import httpx
from stream.tts import get_tts_engine


async def main():
    api_url = os.getenv('SYNTHCAST_API_URL', 'http://localhost:8000')
    youtube_api_key = os.getenv('YOUTUBE_API_KEY')
    video_id = os.getenv('YOUTUBE_VIDEO_ID', '7W_Kbl3SY4M')

    tts = get_tts_engine()

    print("=" * 55)
    print("  SYNTHCAST — YouTube Live Listener")
    print("=" * 55)
    print()

    async with httpx.AsyncClient(timeout=30.0) as http:
        # Start session
        try:
            await http.post(f'{api_url}/session/stop')
            resp = await http.post(f'{api_url}/session/start', json={
                'brand_kit': {
                    'creator_name': os.getenv('CREATOR_NAME', 'Lougens'),
                    'avatar_name': os.getenv('AVATAR_NAME', 'AI Lougens'),
                    'personality': 'confident, warm, direct, never sarcastic',
                    'banned_topics': ['politics', 'religion'],
                    'banned_words': [],
                    'cta_scripts': ['Follow for more!'],
                },
                'llm_provider': 'openai',
                'memory_backend': 'memory',
            })
            s = resp.json()
            print(f"Session: {s['session_id']} | Avatar: {s['avatar']}")
        except Exception as e:
            print(f"Session start failed: {e}")
            return

        # Get YouTube chat ID
        try:
            resp = await http.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={
                    'part': 'liveStreamingDetails',
                    'id': video_id,
                    'key': youtube_api_key,
                }
            )
            data = resp.json()
            items = data.get('items', [])
            if not items:
                print("Video not found or not live. Check your YOUTUBE_VIDEO_ID.")
                return
            details = items[0].get('liveStreamingDetails', {})
            chat_id = details.get('activeLiveChatId')
            if not chat_id:
                print("No active live chat found. Make sure you are live and comments are enabled.")
                return
            print(f"Connected to YouTube chat for video: {video_id}")
            print("Type a comment on your stream to test!")
            print("-" * 55)
        except Exception as e:
            print(f"Failed to get chat ID: {e}")
            return

        # Poll for comments
        page_token = None
        seen = set()

        while True:
            try:
                params = {
                    'liveChatId': chat_id,
                    'part': 'snippet,authorDetails',
                    'key': youtube_api_key,
                    'maxResults': 200,
                }
                if page_token:
                    params['pageToken'] = page_token

                r = await http.get(
                    'https://www.googleapis.com/youtube/v3/liveChat/messages',
                    params=params
                )
                data = r.json()

                for item in data.get('items', []):
                    msg_id = item['id']
                    if msg_id in seen:
                        continue
                    seen.add(msg_id)

                    username = item['authorDetails']['displayName']
                    text = item['snippet'].get('displayMessage', '')

                    if not text:
                        continue

                    print(f"  @{username}: {text}")

                    # Process with agent
                    resp = await http.post(f'{api_url}/comment/process', json={
                        'platform': 'youtube',
                        'username': username,
                        'text': text,
                        'gift_value': 0.0,
                        'is_new_follower': False,
                        'is_subscriber': False,
                    })

                    if resp.text and resp.text.strip() != 'null':
                        result = resp.json()
                        response_text = result['text']
                        print(f"  AI Lougens: {response_text}")
                        print(f"  Speaking...")
                        await tts.speak(response_text, play=True)
                        print()

                page_token = data.get('nextPageToken')
                wait = data.get('pollingIntervalMillis', 5000) / 1000
                await asyncio.sleep(wait)

            except KeyboardInterrupt:
                print("\nStopped.")
                break
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(5)


asyncio.run(main())
