"""
language_detector.py
Detects language of viewer comments and generates responses
in the same language. Supports Haitian Creole, French, Spanish,
English, and others.

Used by youtube_live_listener.py and twitch_listener.py.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Language configs
LANGUAGES = {
    "ht": {
        "name": "Haitian Creole",
        "greeting": "Mèsi pou kòmantè ou a!",
        "system_suffix": "Respond ONLY in Haitian Creole (Kreyòl ayisyen). Keep it natural and warm.",
    },
    "fr": {
        "name": "French",
        "greeting": "Merci pour votre commentaire!",
        "system_suffix": "Respond ONLY in French. Keep it natural and warm.",
    },
    "es": {
        "name": "Spanish",
        "greeting": "¡Gracias por tu comentario!",
        "system_suffix": "Respond ONLY in Spanish. Keep it natural and warm.",
    },
    "pt": {
        "name": "Portuguese",
        "greeting": "Obrigado pelo seu comentário!",
        "system_suffix": "Respond ONLY in Portuguese. Keep it natural and warm.",
    },
    "en": {
        "name": "English",
        "greeting": "Thanks for the comment!",
        "system_suffix": "Respond in English.",
    },
}

# Haitian Creole keywords for fast detection (before calling API)
CREOLE_KEYWORDS = {
    "bonjou", "bonswa", "kijan", "mèsi", "wi", "non", "sa", "ou", "nou",
    "li", "yo", "mwen", "pa", "ak", "nan", "pou", "ki", "gen", "fe", "la",
    "konnen", "vle", "ka", "deja", "toujou", "ankò", "frè", "sè", "kreyòl",
    "ayiti", "ayisyen", "anpil", "trè", "bon", "bèl", "nèg", "cheri"
}

SPANISH_KEYWORDS = {
    "hola", "gracias", "cómo", "qué", "por", "favor", "bueno", "muy",
    "estás", "está", "para", "con", "que", "una", "los", "las", "del"
}

FRENCH_KEYWORDS = {
    "bonjour", "merci", "comment", "pourquoi", "quand", "avec", "pour",
    "vous", "nous", "ils", "une", "les", "des", "est", "très", "bien"
}


def detect_language_fast(text: str) -> str:
    """Fast keyword-based language detection — no API call needed."""
    words = set(text.lower().split())

    creole_matches = len(words & CREOLE_KEYWORDS)
    spanish_matches = len(words & SPANISH_KEYWORDS)
    french_matches = len(words & FRENCH_KEYWORDS)

    # Need at least 2 keyword matches to be confident
    if creole_matches >= 2:
        return "ht"
    if spanish_matches >= 2:
        return "es"
    if french_matches >= 2:
        return "fr"

    return "en"  # default


async def detect_language_api(text: str) -> str:
    """Use OpenAI to detect language — more accurate for short texts."""
    if not OPENAI_API_KEY:
        return detect_language_fast(text)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": f"Detect the language of this text and respond with ONLY the ISO 639-1 code (en, fr, es, ht, pt, etc). Text: '{text}'"
                    }],
                    "max_tokens": 5,
                    "temperature": 0,
                }
            )
            data = resp.json()
            lang = data["choices"][0]["message"]["content"].strip().lower()[:2]
            return lang if lang in LANGUAGES else "en"
    except Exception:
        return detect_language_fast(text)


async def detect_language(text: str) -> str:
    """Detect language — fast check first, API fallback."""
    # Try fast detection first
    fast = detect_language_fast(text)
    if fast != "en":
        return fast  # Confident non-English detection

    # For short ambiguous texts, use API
    if len(text.split()) <= 6:
        return await detect_language_api(text)

    return fast


def get_language_name(lang_code: str) -> str:
    return LANGUAGES.get(lang_code, LANGUAGES["en"])["name"]


def get_system_suffix(lang_code: str) -> str:
    return LANGUAGES.get(lang_code, LANGUAGES["en"])["system_suffix"]


async def generate_multilingual_response(
    comment: str,
    username: str,
    base_system_prompt: str,
    lang_code: str = None
) -> tuple:
    """
    Generate AI response in detected language.
    Returns (response_text, lang_code).
    """
    if lang_code is None:
        lang_code = await detect_language(comment)

    lang_suffix = get_system_suffix(lang_code)
    system = f"{base_system_prompt}\n\n{lang_suffix}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"@{username} says: {comment}"}
                    ],
                    "max_tokens": 80,
                    "temperature": 0.8,
                }
            )
            data = resp.json()
            response = data["choices"][0]["message"]["content"].strip()
            return response, lang_code
    except Exception as e:
        print(f"[AI] Error: {e}")
        return f"Mèsi {username}!" if lang_code == "ht" else f"Thanks {username}!", lang_code
