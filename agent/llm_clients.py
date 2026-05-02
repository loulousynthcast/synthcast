"""
synthcast/agent/llm_clients.py

Pluggable LLM client adapters.
Swap between OpenAI GPT-4o, Anthropic Claude, or a local model
without changing any other code.
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


# ── BASE ─────────────────────────────────────────────────────────────────────

class BaseLLMClient(ABC):
    """All LLM clients implement this interface."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Send prompts, return the response text."""
        ...


# ── OPENAI (GPT-4o) ───────────────────────────────────────────────────────────

class OpenAIClient(BaseLLMClient):
    """
    Uses GPT-4o. Best balance of speed + quality for live response.
    Avg latency: ~600ms — fast enough for real-time.

    REQUIRES:
      pip install openai
      OPENAI_API_KEY env var
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        max_tokens: int = 120,
        temperature: float = 0.75,
        api_key: Optional[str] = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")

        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ]
        )
        return response.choices[0].message.content.strip()


# ── ANTHROPIC (Claude) ────────────────────────────────────────────────────────

class AnthropicClient(BaseLLMClient):
    """
    Uses Claude 3 Haiku — fastest Anthropic model, ~400ms avg.
    Good alternative if you already have Anthropic credits.

    REQUIRES:
      pip install anthropic
      ANTHROPIC_API_KEY env var
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 120,
        temperature: float = 0.75,
        api_key: Optional[str] = None,
    ):
        try:
            import anthropic
        except ImportError:
            raise ImportError("Run: pip install anthropic")

        self.client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return response.content[0].text.strip()


# ── MOCK (for testing without API keys) ───────────────────────────────────────

class MockLLMClient(BaseLLMClient):
    """
    Returns deterministic mock responses.
    Use this for local development and unit tests.
    No API keys needed.
    """

    _RESPONSES = {
        "question":   "That's a great question! Honestly, {username}, I'd say go with what feels right for your audience.",
        "gift":       "Whoa, thank you so much {username}! That genuinely means a lot — I see you!",
        "new_follow": "Welcome to the stream {username}! Really glad you're here.",
        "compliment": "Appreciate that {username}, seriously. This community is everything.",
        "general":    "Hey {username}! Good to see you in the chat.",
    }

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        username = "friend"
        import re
        m = re.search(r"@(\w+)", user_prompt)
        if m:
            username = m.group(1)

        for key, template in self._RESPONSES.items():
            if key in user_prompt.lower():
                return template.format(username=username)

        return self._RESPONSES["general"].format(username=username)


# ── FACTORY ───────────────────────────────────────────────────────────────────

def get_llm_client(provider: str = "mock", **kwargs) -> BaseLLMClient:
    """
    Factory function. Use in your app config:

        client = get_llm_client(provider="openai")
        client = get_llm_client(provider="anthropic")
        client = get_llm_client(provider="mock")   # local dev
    """
    providers = {
        "openai":    OpenAIClient,
        "anthropic": AnthropicClient,
        "mock":      MockLLMClient,
    }
    cls = providers.get(provider.lower())
    if not cls:
        raise ValueError(f"Unknown provider '{provider}'. Choose: {list(providers)}")
    return cls(**kwargs)
