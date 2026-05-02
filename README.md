# Synthcast — Agent Module

The core intelligence layer. Takes a live comment → produces a spoken response.

## What's built

```
synthcast/
├── agent/
│   ├── response_engine.py   ← Main brain: classify → prompt → respond
│   ├── llm_clients.py       ← OpenAI / Anthropic / Mock adapters
│   ├── memory.py            ← Viewer memory (in-memory → Redis)
│   └── queue.py             ← Priority comment queue (thread-safe)
├── tests/
│   └── test_agent.py        ← Full test suite (24 tests, all passing)
└── requirements.txt
```

## Quick start (your part — 5 minutes)

### 1. Clone and install

```bash
git clone <your-repo>
cd synthcast
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set your API key

Create a `.env` file in the project root:

```bash
OPENAI_API_KEY=sk-...your-key-here...
```

Get your key at: https://platform.openai.com/api-keys
Budget: ~$0.01 per 100 responses with GPT-4o-mini. Very cheap.

### 3. Run a quick test

```python
from agent import ResponseEngine, BrandKit, IncomingComment, get_llm_client, get_memory_store
from dotenv import load_dotenv

load_dotenv()

# Configure your brand
kit = BrandKit(
    creator_name="Your Name",
    avatar_name="Your AI Name",
    personality="confident, warm, direct, never sarcastic",
    banned_topics=["politics", "religion"],
    banned_words=[],
    cta_scripts=["Follow for more!", "Subscribe to support!"],
)

# Build the engine
engine = ResponseEngine(
    brand_kit=kit,
    llm_client=get_llm_client("openai"),    # or "mock" for free testing
    memory_store=get_memory_store("memory"),
)

# Simulate a comment
comment = IncomingComment(
    platform="tiktok",
    username="superfan99",
    text="What camera do you use?",
)

response = engine.process(comment)
print(response.text)
# → "I shoot everything on the Sony ZV-E10 — great for the price, superfan99!"
```

### 4. Run the tests

```bash
python -m pytest tests/ -v
```

All 24 tests should pass.

---

## What happens when a comment comes in

```
Comment arrives
      ↓
Brand safety check (banned words / topics)
      ↓
Gift / new follower check (CRITICAL priority)
      ↓
Spam / toxic filter (skip if flagged)
      ↓
Classify: question / compliment / general
      ↓
Build LLM prompt (system = brand kit, user = comment + context)
      ↓
LLM generates response (≤40 words, in creator's voice)
      ↓
Clean output + estimate TTS duration
      ↓
Return AgentResponse → send to ElevenLabs → play on stream
```

---

## Swapping LLM providers

In your config:

```python
# Use OpenAI (best for production)
client = get_llm_client("openai")

# Use Anthropic Claude (if you have credits)
client = get_llm_client("anthropic")

# Use Mock (no API key, for local dev/testing)
client = get_llm_client("mock")
```

---

## Swapping memory backends

```python
# Phase 1 MVP — in-memory (resets each session)
store = get_memory_store("memory")

# Phase 2 — Redis (persists across restarts)
store = get_memory_store("redis", redis_url="redis://localhost:6379")
```

---

## Next modules to be built

- `api/` — FastAPI server exposing the engine as HTTP endpoints
- `stream/` — TikTok Live comment listener
- `tts/` — ElevenLabs voice synthesis wrapper
- `frontend/` — Creator Studio dashboard (React)

---

## Your checklist before testing live

- [ ] OpenAI API key set in `.env`
- [ ] `pip install -r requirements.txt` completed
- [ ] Run `python -m pytest tests/ -v` → all pass
- [ ] Edit `BrandKit` with your real creator name, personality, and banned topics
- [ ] Try the quick start snippet above with a few test comments
- [ ] Tell me what to build next (FastAPI server or TikTok listener)
