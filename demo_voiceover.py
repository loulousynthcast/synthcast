"""
demo_voiceover.py
Generates AI responses and saves them as MP3 files.
Use these MP3s as voiceover in your demo video.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, '.')

import httpx
from elevenlabs.client import AsyncElevenLabs


async def generate():
    api = 'http://localhost:8000'
    el_key = os.getenv('ELEVENLABS_API_KEY')
    voice_id = os.getenv('ELEVENLABS_VOICE_ID')
    client = AsyncElevenLabs(api_key=el_key)

    print("Synthcast Demo — Generating voiceover files")
    print("=" * 50)

    async with httpx.AsyncClient(timeout=30.0) as http:
        await http.post(f'{api}/session/stop')
        await http.post(f'{api}/session/start', json={
            'brand_kit': {
                'creator_name': os.getenv('CREATOR_NAME', 'Lougens'),
                'avatar_name': os.getenv('AVATAR_NAME', 'AI Lougens'),
                'personality': 'confident, warm, direct',
                'banned_topics': ['politics', 'religion'],
                'banned_words': [],
                'cta_scripts': ['Follow for more!'],
            },
            'llm_provider': 'openai',
            'memory_backend': 'memory',
        })

        # Intro narration
        intro_lines = [
            "Welcome to Synthcast. Your AI avatar — always live.",
            "Watch as real viewer comments come in and your AI responds instantly.",
            "Same personality. Your voice. Every platform. Simultaneously.",
        ]

        comments = [
            {'username': 'superfan99', 'text': 'What advice do you have for new content creators?'},
            {'username': 'gifter_king', 'text': 'sent 5x Lion', 'gift_value': 25.0},
            {'username': 'newviewer2025', 'text': 'just followed!', 'is_new_follower': True},
            {'username': 'fan_francais', 'text': 'Salut! Tu fais quoi comme contenu?'},
        ]

        # Generate intro
        print("\nGenerating intro narration...")
        for i, line in enumerate(intro_lines):
            print(f"  Intro {i+1}: {line}")
            audio = b''
            async for chunk in client.text_to_speech.convert(
                text=line,
                voice_id=voice_id,
                model_id=os.getenv('ELEVENLABS_MODEL', 'eleven_multilingual_v2'),
            ):
                audio += chunk
            fname = f'voiceover_intro_{i+1}.mp3'
            with open(fname, 'wb') as f:
                f.write(audio)
            print(f"  Saved: {fname}")

        # Generate comment responses
        print("\nGenerating AI responses...")
        for i, c in enumerate(comments):
            resp = await http.post(f'{api}/comment/process', json={
                'platform': 'tiktok',
                'username': c['username'],
                'text': c['text'],
                'gift_value': c.get('gift_value', 0.0),
                'is_new_follower': c.get('is_new_follower', False),
                'is_subscriber': False,
            })

            if resp.text and resp.text.strip() != 'null':
                result = resp.json()
                response_text = result['text']
                print(f"  @{c['username']}: {c['text']}")
                print(f"  AI: {response_text}")

                audio = b''
                async for chunk in client.text_to_speech.convert(
                    text=response_text,
                    voice_id=voice_id,
                    model_id=os.getenv('ELEVENLABS_MODEL', 'eleven_multilingual_v2'),
                ):
                    audio += chunk

                fname = f'voiceover_response_{i+1}.mp3'
                with open(fname, 'wb') as f:
                    f.write(audio)
                print(f"  Saved: {fname}\n")

            await asyncio.sleep(1)

        # Outro
        outro = "Synthcast. You, synthesized. Sign up free at synthcast dot live."
        print(f"Generating outro: {outro}")
        audio = b''
        async for chunk in client.text_to_speech.convert(
            text=outro,
            voice_id=voice_id,
            model_id=os.getenv('ELEVENLABS_MODEL', 'eleven_multilingual_v2'),
        ):
            audio += chunk
        with open('voiceover_outro.mp3', 'wb') as f:
            f.write(audio)
        print("Saved: voiceover_outro.mp3")

    print()
    print("=" * 50)
    print("All audio files saved in your synthcast folder.")
    print("Import them into CapCut as voiceover.")
    print()
    print("Files generated:")
    print("  voiceover_intro_1.mp3  — opening line")
    print("  voiceover_intro_2.mp3  — explanation")
    print("  voiceover_intro_3.mp3  — platforms")
    print("  voiceover_response_1.mp3 — advice response")
    print("  voiceover_response_2.mp3 — gift response")
    print("  voiceover_response_3.mp3 — follower response")
    print("  voiceover_response_4.mp3 — French response")
    print("  voiceover_outro.mp3    — closing")


asyncio.run(generate())
