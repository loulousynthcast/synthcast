import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()


async def test():
    api_key   = os.getenv("HEYGEN_API_KEY")
    avatar_id = "fb47deecbdbb4ef3bf6ab784e904c7dc"
    look_id   = "4800d1f2af15437db39731be7027ab0c"
    voice_id  = "9f71536fae294bbda05197e87aeed8f3"

    headers = {
        "X-Api-Key":    api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "video_inputs": [{
            "character": {
                "type":           "avatar",
                "avatar_id":      avatar_id,
                "avatar_style":   "normal",
                "avatar_look_id": look_id,
            },
            "voice": {
                "type":       "text",
                "input_text": "Hello everyone, I am AI Louguens. Welcome to the stream.",
                "voice_id":   voice_id,
            },
            "background": {
                "type":  "color",
                "value": "#0D0D0F",
            },
        }],
        "dimension":    {"width": 1080, "height": 1920},
        "aspect_ratio": "9:16",
    }

    print("Submitting render to HeyGen...")
    print("Avatar ID:", avatar_id)
    print("Look ID:  ", look_id)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.heygen.com/v2/video/generate",
            json=payload,
            headers=headers,
        )
        print("Submit status:", resp.status_code)

        if resp.status_code != 200:
            print("Error:", resp.text[:400])
            return

        video_id = resp.json().get("data", {}).get("video_id")
        print("Video ID:", video_id)
        print("Polling... (up to 120 seconds)")

        for i in range(60):
            await asyncio.sleep(2)
            poll = await client.get(
                "https://api.heygen.com/v1/video_status.get",
                params={"video_id": video_id},
                headers=headers,
            )
            data = poll.json().get("data", {})
            status = data.get("status")
            print(f"  [{i*2}s] Status: {status}")

            if status == "completed":
                print("\nSUCCESS!")
                print("Video URL:", data.get("video_url"))
                print("Open that URL in your browser!")
                return
            elif status == "failed":
                print("Failed:", data.get("error"))
                return

        print("Timed out")


asyncio.run(test())
