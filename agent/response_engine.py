"""
synthcast/agent/response_engine.py

The core intelligence layer of Synthcast.
Takes a live comment + stream context → produces a response with:
  - Emotional tone classification
  - Logical / emotional / truthful layered reply
  - Priority scoring (gift, question, new follower, etc.)
  - Brand safety enforcement
  - Viewer memory lookup
"""

import re
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# ── ENUMS ────────────────────────────────────────────────────────────────────

class CommentType(str, Enum):
    QUESTION    = "question"
    COMPLIMENT  = "compliment"
    GIFT        = "gift"
    NEW_FOLLOW  = "new_follow"
    TOXIC       = "toxic"
    GENERAL     = "general"
    SPAM        = "spam"


class ResponseTone(str, Enum):
    WARM        = "warm"
    INFORMATIVE = "informative"
    PLAYFUL     = "playful"
    EMPATHETIC  = "empathetic"
    FIRM        = "firm"       # for borderline content
    SILENT      = "silent"     # no response (spam/toxic)


class Priority(int, Enum):
    CRITICAL  = 10   # gifts, new subscribers
    HIGH      = 7    # direct questions
    MEDIUM    = 5    # compliments, on-topic general
    LOW       = 3    # filler comments
    SKIP      = 0    # spam, toxic, banned


# ── DATA CLASSES ─────────────────────────────────────────────────────────────

@dataclass
class ViewerProfile:
    """What we know about a viewer from memory."""
    username: str
    visit_count: int = 1
    first_seen: float = field(default_factory=time.time)
    last_seen:  float = field(default_factory=time.time)
    gifted_total: float = 0.0
    is_subscriber: bool = False
    notable_facts: list[str] = field(default_factory=list)


@dataclass
class IncomingComment:
    """A raw live comment from any platform."""
    platform: str           # "tiktok" | "instagram" | "twitch" | "youtube"
    username: str
    text: str
    timestamp: float = field(default_factory=time.time)
    gift_value: float = 0.0          # USD equivalent, 0 if no gift
    is_new_follower: bool = False
    is_subscriber: bool = False
    raw_data: dict = field(default_factory=dict)


@dataclass
class ClassifiedComment:
    """A comment after classification."""
    original: IncomingComment
    comment_type: CommentType
    priority: Priority
    sentiment_score: float     # -1.0 (hostile) to 1.0 (positive)
    is_safe: bool              # passed brand safety check
    flagged_reason: Optional[str] = None
    viewer: Optional[ViewerProfile] = None


@dataclass
class AgentResponse:
    """The agent's output for a classified comment."""
    text: str
    tone: ResponseTone
    should_speak: bool          # False = skip this one
    estimated_duration_s: float # approx TTS time
    priority: Priority
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tone"] = self.tone.value
        d["priority"] = int(self.priority.value)
        return d


# ── BRAND KIT ────────────────────────────────────────────────────────────────

@dataclass
class BrandKit:
    """Creator's configured brand rules. Loaded from DB at session start."""
    creator_name: str
    avatar_name: str
    personality: str        # e.g. "confident, warm, direct, never sarcastic"
    tone_adjectives: list[str] = field(default_factory=list)
    banned_topics: list[str] = field(default_factory=list)
    banned_words: list[str]  = field(default_factory=list)
    approved_topics: list[str] = field(default_factory=list)
    cta_scripts: list[str]   = field(default_factory=list)
    language: str = "en"
    humor_level: int = 5    # 1–10
    formality: int = 5      # 1 (very casual) – 10 (very formal)
    max_response_words: int = 40

    @classmethod
    def default(cls) -> "BrandKit":
        return cls(
            creator_name="Creator",
            avatar_name="AI Avatar",
            personality="confident, warm, genuine, never dismissive",
            tone_adjectives=["direct", "honest", "friendly"],
            banned_topics=["politics", "religion", "competitors"],
            banned_words=["hate", "kill", "slur"],
            approved_topics=["content creation", "lifestyle", "the stream"],
            cta_scripts=["Follow for more!", "Subscribe to support the stream!"],
            language="en",
        )


# ── CLASSIFIER ───────────────────────────────────────────────────────────────

class CommentClassifier:
    """
    Rule-based first pass classification.
    Fast, no LLM cost, catches 80% of cases.
    LLM handles the rest in the response engine.
    """

    QUESTION_PATTERNS = [
        r"\?",
        r"\b(what|when|where|who|why|how|can you|do you|are you|is it|will you)\b",
        r"\b(tell me|explain|describe|show me)\b",
    ]

    GIFT_KEYWORDS    = ["gift", "rose", "diamond", "coin", "sent", "donated"]
    FOLLOW_KEYWORDS  = ["followed", "just followed", "new follow", "subscribed"]
    SPAM_PATTERNS    = [
        r"(.)\1{6,}",                       # aaaaaaaaa
        r"https?://\S+",                     # links
        r"\b(follow me|check my|visit my)\b",
        r"^(.{1,3})$",                       # too short to process
    ]
    TOXIC_KEYWORDS   = [
        "idiot", "stupid", "ugly", "trash", "garbage",
        "kill", "hate", "racist", "die",
    ]

    def classify(
        self,
        comment: IncomingComment,
        brand_kit: BrandKit,
        viewer: Optional[ViewerProfile] = None,
    ) -> ClassifiedComment:

        text_lower = comment.text.lower()

        # ── Brand safety check ─────────────────────────────
        for word in brand_kit.banned_words:
            if word.lower() in text_lower:
                return ClassifiedComment(
                    original=comment,
                    comment_type=CommentType.TOXIC,
                    priority=Priority.SKIP,
                    sentiment_score=-1.0,
                    is_safe=False,
                    flagged_reason=f"banned word: {word}",
                    viewer=viewer,
                )

        for topic in brand_kit.banned_topics:
            if topic.lower() in text_lower:
                return ClassifiedComment(
                    original=comment,
                    comment_type=CommentType.GENERAL,
                    priority=Priority.SKIP,
                    sentiment_score=0.0,
                    is_safe=False,
                    flagged_reason=f"banned topic: {topic}",
                    viewer=viewer,
                )

        # ── Toxic ──────────────────────────────────────────
        if any(kw in text_lower for kw in self.TOXIC_KEYWORDS):
            return ClassifiedComment(
                original=comment,
                comment_type=CommentType.TOXIC,
                priority=Priority.SKIP,
                sentiment_score=-1.0,
                is_safe=True,   # safe to log, just don't respond
                flagged_reason="toxic language",
                viewer=viewer,
            )

        # ── Gift (always check before spam — gift value overrides short text) ──
        if comment.gift_value > 0 or any(kw in text_lower for kw in self.GIFT_KEYWORDS):
            return ClassifiedComment(
                original=comment,
                comment_type=CommentType.GIFT,
                priority=Priority.CRITICAL,
                sentiment_score=1.0,
                is_safe=True,
                viewer=viewer,
            )

        # ── New follower (before spam too) ─────────────────
        if comment.is_new_follower or any(kw in text_lower for kw in self.FOLLOW_KEYWORDS):
            return ClassifiedComment(
                original=comment,
                comment_type=CommentType.NEW_FOLLOW,
                priority=Priority.CRITICAL,
                sentiment_score=1.0,
                is_safe=True,
                viewer=viewer,
            )

        # ── Spam ───────────────────────────────────────────
        for pattern in self.SPAM_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return ClassifiedComment(
                    original=comment,
                    comment_type=CommentType.SPAM,
                    priority=Priority.SKIP,
                    sentiment_score=0.0,
                    is_safe=True,
                    flagged_reason="spam pattern",
                    viewer=viewer,
                )

        # ── New follower ───────────────────────────────────
        if comment.is_new_follower or any(kw in text_lower for kw in self.FOLLOW_KEYWORDS):
            return ClassifiedComment(
                original=comment,
                comment_type=CommentType.NEW_FOLLOW,
                priority=Priority.CRITICAL,
                sentiment_score=1.0,
                is_safe=True,
                viewer=viewer,
            )

        # ── Question ───────────────────────────────────────
        for pattern in self.QUESTION_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return ClassifiedComment(
                    original=comment,
                    comment_type=CommentType.QUESTION,
                    priority=Priority.HIGH,
                    sentiment_score=0.6,
                    is_safe=True,
                    viewer=viewer,
                )

        # ── Compliment ─────────────────────────────────────
        compliment_words = ["love", "amazing", "great", "awesome", "fire", "goat", "best", "incredible"]
        if any(w in text_lower for w in compliment_words):
            return ClassifiedComment(
                original=comment,
                comment_type=CommentType.COMPLIMENT,
                priority=Priority.MEDIUM,
                sentiment_score=0.9,
                is_safe=True,
                viewer=viewer,
            )

        # ── General ────────────────────────────────────────
        return ClassifiedComment(
            original=comment,
            comment_type=CommentType.GENERAL,
            priority=Priority.LOW,
            sentiment_score=0.3,
            is_safe=True,
            viewer=viewer,
        )


# ── PROMPT BUILDER ───────────────────────────────────────────────────────────

class PromptBuilder:
    """
    Builds the system + user prompt sent to the LLM.
    The system prompt encodes the creator's full brand kit.
    """

    def build_system_prompt(self, brand_kit: BrandKit) -> str:
        banned = ", ".join(brand_kit.banned_topics) or "none"
        tone   = ", ".join(brand_kit.tone_adjectives) or "warm and direct"
        ctas   = " | ".join(brand_kit.cta_scripts[:2]) if brand_kit.cta_scripts else ""

        return f"""You are {brand_kit.avatar_name}, the AI live stream presence for {brand_kit.creator_name}.

PERSONALITY: {brand_kit.personality}
TONE: {tone}
HUMOR LEVEL: {brand_kit.humor_level}/10 (1=serious, 10=comedian)
FORMALITY: {brand_kit.formality}/10 (1=very casual, 10=very formal)
LANGUAGE: {brand_kit.language}

RULES — follow these without exception:
1. Never mention you are an AI unless directly and sincerely asked.
2. Never discuss: {banned}.
3. Keep ALL responses under {brand_kit.max_response_words} words. Brevity wins.
4. Speak in FIRST PERSON as {brand_kit.creator_name}. Say "I" not "the creator".
5. Sound HUMAN — natural cadence, not robotic. Use contractions.
6. Be TRUTHFUL. Never fabricate facts about {brand_kit.creator_name}'s life.
7. If asked something you don't know, say so naturally: "Honestly not sure on that one."
8. Use the viewer's username when responding to them — it feels personal.
9. LANGUAGE DETECTION — CRITICAL: Automatically detect what language the viewer is writing in and respond in THAT SAME LANGUAGE. French viewer writes in French → respond in French. Spanish → Spanish. Haitian Creole → Haitian Creole. English → English. Always match the viewer's language. Same personality, same voice, different language.
{f'9. Occasionally (not every response) include a call to action: {ctas}' if ctas else ''}

RESPONSE FORMAT:
Return ONLY the spoken response text. No quotes, no labels, no JSON.
The text will be fed directly to text-to-speech. Write it to be heard, not read."""

    def build_user_prompt(
        self,
        classified: ClassifiedComment,
        brand_kit: BrandKit,
        recent_context: list[str],
    ) -> str:
        c = classified.original
        v = classified.viewer
        ctx = "\n".join(f"  - {x}" for x in recent_context[-4:]) if recent_context else "  (none)"

        viewer_info = ""
        if v:
            if v.visit_count > 1:
                viewer_info = f"\nVIEWER MEMORY: {v.username} has been here {v.visit_count} times."
            if v.is_subscriber:
                viewer_info += f" They are a subscriber."
            if v.gifted_total > 0:
                viewer_info += f" They've sent ${v.gifted_total:.2f} in gifts total."
            if v.notable_facts:
                viewer_info += f" Known facts: {'; '.join(v.notable_facts[:2])}."

        type_instructions = {
            CommentType.GIFT: (
                f"@{c.username} just sent a gift worth ${c.gift_value:.2f}. "
                f"Thank them warmly, specifically, and authentically. Make it feel genuine, not scripted."
            ),
            CommentType.NEW_FOLLOW: (
                f"@{c.username} just followed. Welcome them like you mean it — "
                f"brief, warm, personal. Don't be generic."
            ),
            CommentType.QUESTION: (
                f"@{c.username} asked: \"{c.text}\"\n"
                f"Answer honestly, conversationally, in your voice. "
                f"If you don't know, say so naturally."
            ),
            CommentType.COMPLIMENT: (
                f"@{c.username} said: \"{c.text}\"\n"
                f"Acknowledge it graciously. Be real, not sycophantic. "
                f"Maybe turn it back to them or the community."
            ),
            CommentType.GENERAL: (
                f"@{c.username} said: \"{c.text}\"\n"
                f"Respond naturally as you would mid-stream. Keep it brief and engaging."
            ),
        }

        instruction = type_instructions.get(
            classified.comment_type,
            f'@{c.username} said: "{c.text}"\nRespond naturally.'
        )

        return f"""RECENT STREAM CONTEXT (last few exchanges):
{ctx}
{viewer_info}

CURRENT COMMENT TO RESPOND TO:
Platform: {c.platform}
{instruction}

Respond now. Under {brand_kit.max_response_words} words. Sound like yourself."""


# ── RESPONSE ENGINE ───────────────────────────────────────────────────────────

class ResponseEngine:
    """
    Main entry point. Orchestrates:
    1. Classification
    2. Priority check
    3. Prompt construction
    4. LLM call (pluggable — works with OpenAI, Anthropic, or local)
    5. Post-processing & safety
    """

    def __init__(
        self,
        brand_kit: BrandKit,
        llm_client,               # injected — see llm_clients.py
        memory_store=None,        # injected — see memory.py
        min_priority: Priority = Priority.LOW,
    ):
        self.brand_kit     = brand_kit
        self.llm           = llm_client
        self.memory        = memory_store
        self.min_priority  = min_priority
        self.classifier    = CommentClassifier()
        self.prompt_builder = PromptBuilder()
        self._context_window: list[str] = []   # rolling last-N exchanges

    def process(self, comment: IncomingComment) -> Optional[AgentResponse]:
        """
        Full pipeline: comment in → response out (or None if skipped).
        """
        # 1. Viewer memory lookup
        viewer = None
        if self.memory:
            viewer = self.memory.get_viewer(comment.platform, comment.username)

        # 2. Classify
        classified = self.classifier.classify(comment, self.brand_kit, viewer)

        # 3. Priority gate
        if classified.priority < self.min_priority or classified.priority == Priority.SKIP:
            return None

        # 4. Build prompts
        system_prompt = self.prompt_builder.build_system_prompt(self.brand_kit)
        user_prompt   = self.prompt_builder.build_user_prompt(
            classified, self.brand_kit, self._context_window
        )

        # 5. LLM call
        raw_text = self.llm.complete(system_prompt, user_prompt)

        # 6. Post-process
        response_text = self._clean(raw_text)

        # 7. Update context window
        self._update_context(comment.username, response_text)

        # 8. Update viewer memory
        if self.memory and viewer:
            self.memory.update_viewer(comment.platform, comment.username, comment)

        # 9. Estimate TTS duration (~130 words/min average speech rate)
        word_count = len(response_text.split())
        duration_s = round((word_count / 130) * 60, 1)

        return AgentResponse(
            text=response_text,
            tone=self._infer_tone(classified),
            should_speak=True,
            estimated_duration_s=duration_s,
            priority=classified.priority,
            metadata={
                "username": comment.username,
                "comment_type": classified.comment_type.value,
                "platform": comment.platform,
                "word_count": word_count,
            }
        )

    def _clean(self, text: str) -> str:
        """Strip quotes, labels, and excess whitespace from LLM output."""
        text = text.strip()
        text = re.sub(r'^["\'"]|["\'"]$', '', text)
        text = re.sub(r'^(Response:|Reply:|Agent:)\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _infer_tone(self, classified: ClassifiedComment) -> ResponseTone:
        mapping = {
            CommentType.GIFT:       ResponseTone.WARM,
            CommentType.NEW_FOLLOW: ResponseTone.WARM,
            CommentType.QUESTION:   ResponseTone.INFORMATIVE,
            CommentType.COMPLIMENT: ResponseTone.PLAYFUL,
            CommentType.GENERAL:    ResponseTone.WARM,
            CommentType.TOXIC:      ResponseTone.SILENT,
            CommentType.SPAM:       ResponseTone.SILENT,
        }
        return mapping.get(classified.comment_type, ResponseTone.WARM)

    def _update_context(self, username: str, response: str):
        entry = f"@{username} → {response[:60]}{'...' if len(response) > 60 else ''}"
        self._context_window.append(entry)
        if len(self._context_window) > 8:
            self._context_window.pop(0)
