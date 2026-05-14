import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


async def test():
    from elevenlabs.client import AsyncElevenLabs

    api_key  = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    client   = AsyncElevenLabs(api_key=api_key)

    variations = [
        ("Louguens", "Hello, I am Louguens. Welcome to the stream."),
        ("Lougens",  "Hello, I am Lougens. Welcome to the stream."),
        ("Loo-gehns","Hello, I am Loo-gehns. Welcome to the stream."),
        ("Loo-gahn", "Hello, I am Loo-gahn. Welcome to the stream."),
    ]

    print("Generating pronunciation variations...")
    print("Listen to each file and pick the one that sounds right.")
    print()

    for name, text in variations:
        print(f"Generating: {name}")
        audio_bytes = b""
        async for chunk in client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id="eleven_turbo_v2",
        ):
            audio_bytes += chunk

        filename = f"name_{name.replace('-','_').replace(' ','_')}.mp3"
        with open(filename, "wb") as f:
            f.write(audio_bytes)
        print(f"  Saved: {filename}")

    print()
    print("Open each MP3 file and listen.")
    print("Tell me which one sounds most like your name!")


asyncio.run(test())
