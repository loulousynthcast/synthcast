"""
demo.py — Synthcast Demo Script
Run this during your demo recording.
Shows real AI responses spoken in your cloned voice.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, '.')

from stream.tts import get_tts_engine
import httpx


async def demo():
    api = 'http://localhost:8000'
    tts = get_tts_engine()

    print()
    print("=" * 55)
    print("  SYNTHCAST — AI AVATAR LIVE DEMO")
    print("=" * 55)
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Start session
        await client.post(f'{api}/session/stop')
        await client.post(f'{api}/session/start', json={
            'brand_kit': {
                'creator_name': os.getenv('CREATOR_NAME', 'Lougens'),
                'avatar_name': os.getenv('AVATAR_NAME', 'AI Lougens'),
                'personality': 'confident, warm, direct, never sarcastic',
                'banned_topics': ['politics', 'religion'],
                'banned_words': [],
                'cta_scripts': ['Follow for more!', 'Subscribe!'],
            },
            'llm_provider': 'openai',
            'memory_backend': 'memory',
        })

        comments = [
            {
                'platform': 'tiktok',
                'username': 'superfan99',
                'text': 'What advice do you have for new content creators?',
                'gift_value': 0.0,
                'is_new_follower': False,
                'is_subscriber': False,
            },
            {
                'platform': 'tiktok',
                'username': 'gifter_king',
                'text': 'sent 5x Lion',
                'gift_value': 25.0,
                'is_new_follower': False,
                'is_subscriber': False,
            },
            {
                'platform': 'tiktok',
                'username': 'newviewer2025',
                'text': 'just followed!',
                'gift_value': 0.0,
                'is_new_follower': True,
                'is_subscriber': False,
            },
            {
                'platform': 'tiktok',
                'username': 'fan_francais',
                'text': 'Salut! Tu fais quoi comme contenu exactement?',
                'gift_value': 0.0,
                'is_new_follower': False,
                'is_subscriber': False,
            },
        ]

        for c in comments:
            print(f"  VIEWER @{c['username']}:")
            print(f"  \"{c['text']}\"")
            print()

            resp = await client.post(f'{api}/comment/process', json=c)

            if resp.text and resp.text.strip() != 'null':
                result = resp.json()
                print(f"  AI LOUGENS:")
                print(f"  \"{result['text']}\"")
                print()
                print("  [Speaking in your voice...]")
                await tts.speak(result['text'], play=True)
                print()
                print("-" * 55)
                print()
            else:
                print("  [Comment filtered]")
                print()

            await asyncio.sleep(1.5)

    print("=" * 55)
    print("  SYNTHCAST — synthcast.live")
    print("  You, Synthesized.")
    print("=" * 55)


asyncio.run(demo())
