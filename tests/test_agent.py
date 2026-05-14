"""
synthcast/tests/test_agent.py

Full test suite for the agent response engine.
Run with: python -m pytest tests/ -v

No API keys needed — uses MockLLMClient throughout.
"""

import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.response_engine import (
    IncomingComment, BrandKit, CommentClassifier,
    PromptBuilder, ResponseEngine,
    CommentType, Priority, ResponseTone
)
from agent.llm_clients import MockLLMClient
from agent.memory import InMemoryViewerStore
from agent.queue import CommentQueue


# ── FIXTURES ──────────────────────────────────────────────────────────────────

def make_comment(text="hello", username="viewer1", platform="tiktok",
                 gift=0.0, new_follow=False, is_sub=False):
    return IncomingComment(
        platform=platform,
        username=username,
        text=text,
        gift_value=gift,
        is_new_follower=new_follow,
        is_subscriber=is_sub,
    )


def make_brand_kit(**overrides):
    kit = BrandKit.default()
    for k, v in overrides.items():
        setattr(kit, k, v)
    return kit


def make_engine(brand_kit=None, memory=None):
    return ResponseEngine(
        brand_kit=brand_kit or make_brand_kit(),
        llm_client=MockLLMClient(),
        memory_store=memory or InMemoryViewerStore(),
    )


# ── CLASSIFIER TESTS ──────────────────────────────────────────────────────────

class TestClassifier:

    def setup_method(self):
        self.clf = CommentClassifier()
        self.kit = make_brand_kit()

    def test_question_mark_detected(self):
        c = make_comment("What's your setup?")
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.QUESTION
        assert result.priority >= Priority.HIGH

    def test_how_keyword_is_question(self):
        c = make_comment("how do you edit your videos")
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.QUESTION

    def test_gift_detected_by_value(self):
        c = make_comment("here you go", gift=5.00)
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.GIFT
        assert result.priority == Priority.CRITICAL

    def test_new_follower_detected(self):
        c = make_comment("just followed!", new_follow=True)
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.NEW_FOLLOW
        assert result.priority == Priority.CRITICAL

    def test_spam_link_skipped(self):
        c = make_comment("check out https://spam.com/follow-me")
        result = self.clf.classify(c, self.kit)
        assert result.priority == Priority.SKIP

    def test_spam_repetition_skipped(self):
        c = make_comment("aaaaaaaaaaaaa")
        result = self.clf.classify(c, self.kit)
        assert result.priority == Priority.SKIP

    def test_toxic_skipped(self):
        c = make_comment("you're an idiot")
        result = self.clf.classify(c, self.kit)
        assert result.priority == Priority.SKIP

    def test_banned_word_blocked(self):
        kit = make_brand_kit(banned_words=["garbage"])
        c = make_comment("this content is garbage")
        result = self.clf.classify(c, kit)
        assert result.priority == Priority.SKIP
        assert result.is_safe is False

    def test_banned_topic_blocked(self):
        kit = make_brand_kit(banned_topics=["politics"])
        c = make_comment("what do you think about the election")
        result = self.clf.classify(c, kit)
        assert result.priority == Priority.SKIP

    def test_compliment_detected(self):
        c = make_comment("you're amazing, love this stream")
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.COMPLIMENT
        assert result.sentiment_score > 0.5

    def test_general_comment(self):
        c = make_comment("hey everyone")
        result = self.clf.classify(c, self.kit)
        assert result.comment_type == CommentType.GENERAL
        assert result.priority == Priority.LOW


# ── PROMPT BUILDER TESTS ──────────────────────────────────────────────────────

class TestPromptBuilder:

    def setup_method(self):
        self.builder = PromptBuilder()
        self.kit = make_brand_kit(
            creator_name="Alex",
            avatar_name="AI Alex",
            personality="confident and warm",
            banned_topics=["politics"],
        )

    def test_system_prompt_includes_creator_name(self):
        prompt = self.builder.build_system_prompt(self.kit)
        assert "Alex" in prompt

    def test_system_prompt_includes_banned_topics(self):
        prompt = self.builder.build_system_prompt(self.kit)
        assert "politics" in prompt

    def test_system_prompt_includes_word_limit(self):
        prompt = self.builder.build_system_prompt(self.kit)
        assert str(self.kit.max_response_words) in prompt

    def test_user_prompt_includes_username(self):
        from agent.response_engine import ClassifiedComment
        c = make_comment("great stream!", username="superfan99")
        classified = ClassifiedComment(
            original=c,
            comment_type=CommentType.COMPLIMENT,
            priority=Priority.MEDIUM,
            sentiment_score=0.9,
            is_safe=True,
        )
        prompt = self.builder.build_user_prompt(classified, self.kit, [])
        assert "superfan99" in prompt

    def test_user_prompt_includes_platform(self):
        from agent.response_engine import ClassifiedComment
        c = make_comment("hello", platform="twitch")
        classified = ClassifiedComment(
            original=c,
            comment_type=CommentType.GENERAL,
            priority=Priority.LOW,
            sentiment_score=0.3,
            is_safe=True,
        )
        prompt = self.builder.build_user_prompt(classified, self.kit, [])
        assert "twitch" in prompt.lower()


# ── RESPONSE ENGINE TESTS ─────────────────────────────────────────────────────

class TestResponseEngine:

    def setup_method(self):
        self.engine = make_engine()

    def test_question_gets_response(self):
        c = make_comment("What camera do you use?")
        result = self.engine.process(c)
        assert result is not None
        assert result.should_speak is True
        assert len(result.text) > 0

    def test_gift_gets_response(self):
        c = make_comment("here!", gift=10.0)
        result = self.engine.process(c)
        assert result is not None
        assert result.priority == Priority.CRITICAL

    def test_spam_returns_none(self):
        c = make_comment("aaaaaaaaaaaaaaaa")
        result = self.engine.process(c)
        assert result is None

    def test_toxic_returns_none(self):
        c = make_comment("you're an idiot")
        result = self.engine.process(c)
        assert result is None

    def test_response_text_is_clean(self):
        c = make_comment("Do you enjoy streaming?")
        result = self.engine.process(c)
        assert result is not None
        assert not result.text.startswith('"')
        assert not result.text.startswith("Response:")

    def test_duration_estimate_positive(self):
        c = make_comment("What inspired you to start streaming?")
        result = self.engine.process(c)
        assert result is not None
        assert result.estimated_duration_s > 0

    def test_context_window_grows(self):
        for i in range(5):
            c = make_comment(f"message {i}?", username=f"user{i}")
            self.engine.process(c)
        assert len(self.engine._context_window) > 0

    def test_context_window_max_size(self):
        for i in range(20):
            c = make_comment(f"question {i}?", username=f"user{i}")
            self.engine.process(c)
        assert len(self.engine._context_window) <= 8

    def test_to_dict_serializable(self):
        import json
        c = make_comment("What's your favorite game?")
        result = self.engine.process(c)
        assert result is not None
        serialized = json.dumps(result.to_dict())
        assert "text" in serialized

    def test_banned_word_blocked_end_to_end(self):
        kit = make_brand_kit(banned_words=["garbage"])
        engine = make_engine(brand_kit=kit)
        c = make_comment("this is garbage content")
        result = engine.process(c)
        assert result is None


# ── MEMORY TESTS ──────────────────────────────────────────────────────────────

class TestMemory:

    def setup_method(self):
        self.store = InMemoryViewerStore()

    def test_new_viewer_returns_none(self):
        result = self.store.get_viewer("tiktok", "newuser")
        assert result is None

    def test_viewer_created_on_update(self):
        c = make_comment(username="fan1", platform="tiktok")
        self.store.update_viewer("tiktok", "fan1", c)
        viewer = self.store.get_viewer("tiktok", "fan1")
        assert viewer is not None
        assert viewer.username == "fan1"

    def test_visit_count_increments(self):
        c = make_comment(username="fan1")
        self.store.update_viewer("tiktok", "fan1", c)
        self.store.update_viewer("tiktok", "fan1", c)
        self.store.update_viewer("tiktok", "fan1", c)
        viewer = self.store.get_viewer("tiktok", "fan1")
        assert viewer.visit_count == 3

    def test_gift_total_accumulates(self):
        c1 = make_comment(username="gifter1", gift=5.0)
        c2 = make_comment(username="gifter1", gift=10.0)
        self.store.update_viewer("tiktok", "gifter1", c1)
        self.store.update_viewer("tiktok", "gifter1", c2)
        viewer = self.store.get_viewer("tiktok", "gifter1")
        assert viewer.gifted_total == 15.0

    def test_facts_added(self):
        c = make_comment(username="fan1")
        self.store.update_viewer("tiktok", "fan1", c)
        self.store.add_fact("tiktok", "fan1", "loves cooking streams")
        viewer = self.store.get_viewer("tiktok", "fan1")
        assert "loves cooking streams" in viewer.notable_facts

    def test_stats_correct(self):
        c_sub = make_comment(username="subber", is_sub=True)
        c_gift = make_comment(username="gifter", gift=20.0)
        c_reg = make_comment(username="regular")
        for c in [c_sub, c_gift, c_reg]:
            self.store.update_viewer("tiktok", c.username, c)
        stats = self.store.get_stats()
        assert stats["total_unique_viewers"] == 3
        assert stats["subscribers"] == 1
        assert stats["gifters"] == 1
        assert stats["total_gift_revenue"] == 20.0

    def test_platform_separation(self):
        c = make_comment(username="crossplatform")
        self.store.update_viewer("tiktok", "crossplatform", c)
        self.store.update_viewer("instagram", "crossplatform", c)
        tk = self.store.get_viewer("tiktok", "crossplatform")
        ig = self.store.get_viewer("instagram", "crossplatform")
        assert tk is not None
        assert ig is not None
        assert tk.visit_count == 1
        assert ig.visit_count == 1


# ── QUEUE TESTS ───────────────────────────────────────────────────────────────

class TestQueue:

    def test_push_and_pop(self):
        q = CommentQueue()
        c = make_comment("hello")
        q.push(c, Priority.MEDIUM)
        result = q.pop(timeout=0.2)
        assert result is not None
        assert result.text == "hello"

    def test_priority_ordering(self):
        q = CommentQueue()
        low  = make_comment("low priority",  username="u1")
        high = make_comment("high priority", username="u2", gift=5.0)
        low._priority_hint  = Priority.LOW
        high._priority_hint = Priority.CRITICAL

        q.push(low,  Priority.LOW)
        q.push(high, Priority.CRITICAL)

        first = q.pop(timeout=0.2)
        assert first.username == "u2"   # gift (CRITICAL) comes first

    def test_empty_returns_none(self):
        q = CommentQueue()
        result = q.pop(timeout=0.1)
        assert result is None

    def test_size_tracking(self):
        q = CommentQueue()
        assert q.size() == 0
        q.push(make_comment("a"), Priority.LOW)
        q.push(make_comment("b"), Priority.LOW)
        assert q.size() == 2
        q.pop(timeout=0.1)
        assert q.size() == 1

    def test_stats_structure(self):
        q = CommentQueue()
        q.push(make_comment("x"), Priority.HIGH)
        q.pop(timeout=0.1)
        stats = q.stats()
        assert "total_received" in stats
        assert "total_processed" in stats
        assert stats["total_received"] == 1
        assert stats["total_processed"] == 1


# ── RUN ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
