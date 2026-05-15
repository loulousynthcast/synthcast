"""
synthcast/billing/stripe_billing.py

Stripe billing integration for Synthcast.
Handles creator subscriptions, webhooks, and tier management.

Tiers:
  free    — $0/mo   — 1 platform, 2hr sessions, basic voice
  creator — $29/mo  — 3 platforms, unlimited, voice clone, analytics
  pro     — $79/mo  — all 5 platforms, video avatar, brand deals, white-label

Install:
    pip install stripe fastapi

YOUR PART:
  1. Sign up at stripe.com (free)
  2. Get your API keys from stripe.com/dashboard/apikeys
  3. Add to .env:
       STRIPE_SECRET_KEY=sk_test_...
       STRIPE_WEBHOOK_SECRET=whsec_...
       STRIPE_CREATOR_PRICE_ID=price_...
       STRIPE_PRO_PRICE_ID=price_...
  4. Create products in Stripe dashboard (instructions in README)
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum
from dotenv import load_dotenv

load_dotenv()


# ── TIERS ────────────────────────────────────────────────────────────────────

class CreatorTier(str, Enum):
    FREE    = "free"
    CREATOR = "creator"
    PRO     = "pro"


@dataclass
class TierConfig:
    name: str
    price_monthly: float
    platforms: int          # max simultaneous platforms
    session_limit_hrs: Optional[float]   # None = unlimited
    voice_clone: bool
    video_avatar: bool
    analytics: bool
    brand_deals: bool
    white_label: bool
    memory_backend: str     # "memory" or "redis"
    max_response_words: int
    stripe_price_id: Optional[str] = None


TIER_CONFIGS = {
    CreatorTier.FREE: TierConfig(
        name="Free",
        price_monthly=0.0,
        platforms=1,
        session_limit_hrs=2.0,
        voice_clone=False,
        video_avatar=False,
        analytics=False,
        brand_deals=False,
        white_label=False,
        memory_backend="memory",
        max_response_words=30,
        stripe_price_id=None,
    ),
    CreatorTier.CREATOR: TierConfig(
        name="Creator",
        price_monthly=29.0,
        platforms=3,
        session_limit_hrs=None,
        voice_clone=True,
        video_avatar=False,
        analytics=True,
        brand_deals=False,
        white_label=False,
        memory_backend="redis",
        max_response_words=50,
        stripe_price_id=os.getenv("STRIPE_CREATOR_PRICE_ID"),
    ),
    CreatorTier.PRO: TierConfig(
        name="Pro",
        price_monthly=79.0,
        platforms=5,
        session_limit_hrs=None,
        voice_clone=True,
        video_avatar=True,
        analytics=True,
        brand_deals=True,
        white_label=True,
        memory_backend="redis",
        max_response_words=60,
        stripe_price_id=os.getenv("STRIPE_PRO_PRICE_ID"),
    ),
}


# ── CREATOR ACCOUNT ───────────────────────────────────────────────────────────

# Synthcast master API keys (set in Railway env vars)
# Pro/Studio creators use these — no need to bring their own
SYNTHCAST_ELEVENLABS_KEY = os.getenv("SYNTHCAST_ELEVENLABS_API_KEY", "")
SYNTHCAST_HEYGEN_KEY     = os.getenv("SYNTHCAST_HEYGEN_API_KEY", "")
SYNTHCAST_OPENAI_KEY     = os.getenv("SYNTHCAST_OPENAI_API_KEY", "")

# Tiers that use Synthcast master keys
MANAGED_TIERS = {CreatorTier.PRO}


@dataclass
class CreatorAccount:
    """Represents a Synthcast creator in the billing system."""
    creator_id: str
    email: str
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    tier: CreatorTier = CreatorTier.FREE
    is_active: bool = True
    trial_ends_at: Optional[float] = None
    created_at: float = field(default_factory=lambda: __import__('time').time())

    # Creator-provided API keys (used on Free and Creator tiers)
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    heygen_api_key: Optional[str] = None
    heygen_avatar_id: Optional[str] = None
    openai_api_key: Optional[str] = None

    @property
    def uses_managed_keys(self) -> bool:
        """Pro and Studio tiers use Synthcast master API keys."""
        return self.tier in MANAGED_TIERS

    def get_elevenlabs_key(self) -> str:
        """Return the correct ElevenLabs key based on tier."""
        if self.uses_managed_keys:
            return SYNTHCAST_ELEVENLABS_KEY
        return self.elevenlabs_api_key or ""

    def get_heygen_key(self) -> str:
        """Return the correct HeyGen key based on tier."""
        if self.uses_managed_keys:
            return SYNTHCAST_HEYGEN_KEY
        return self.heygen_api_key or ""

    def get_openai_key(self) -> str:
        """Return the correct OpenAI key based on tier."""
        if self.uses_managed_keys and SYNTHCAST_OPENAI_KEY:
            return SYNTHCAST_OPENAI_KEY
        return self.openai_api_key or os.getenv("OPENAI_API_KEY", "")

    def has_voice_configured(self) -> bool:
        """Check if voice is ready to use."""
        if self.uses_managed_keys:
            return bool(SYNTHCAST_ELEVENLABS_KEY and self.elevenlabs_voice_id)
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id)

    def has_avatar_configured(self) -> bool:
        """Check if video avatar is ready to use."""
        if self.uses_managed_keys:
            return bool(SYNTHCAST_HEYGEN_KEY and self.heygen_avatar_id)
        return bool(self.heygen_api_key and self.heygen_avatar_id)

    def missing_keys(self) -> list[str]:
        """Return list of missing API keys for this tier."""
        missing = []
        if self.tier in [CreatorTier.FREE, CreatorTier.CREATOR]:
            if not self.elevenlabs_api_key:
                missing.append("ElevenLabs API key")
            if not self.elevenlabs_voice_id:
                missing.append("ElevenLabs Voice ID")
            if not self.openai_api_key:
                missing.append("OpenAI API key")
        return missing

    @property
    def config(self) -> TierConfig:
        return TIER_CONFIGS[self.tier]

    def can_use_platform(self, platform_count: int) -> bool:
        return platform_count <= self.config.platforms

    def can_go_live(self, session_duration_hrs: float = 0) -> tuple[bool, str]:
        if not self.is_active:
            return False, "Account inactive"
        limit = self.config.session_limit_hrs
        if limit and session_duration_hrs >= limit:
            return False, f"Free tier limit: {limit}hr per session. Upgrade to Creator for unlimited."
        return True, "ok"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        d["config"] = {
            "name": self.config.name,
            "price_monthly": self.config.price_monthly,
            "platforms": self.config.platforms,
            "voice_clone": self.config.voice_clone,
            "video_avatar": self.config.video_avatar,
            "analytics": self.config.analytics,
        }
        return d


# ── STRIPE CLIENT ─────────────────────────────────────────────────────────────

class StripeClient:
    """
    Wraps Stripe API calls for Synthcast billing.
    All methods are synchronous — wrap in asyncio.run_in_executor for async.
    """

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or os.getenv("STRIPE_SECRET_KEY")
        if not self.secret_key:
            raise ValueError(
                "STRIPE_SECRET_KEY not set.\n"
                "Get it at: https://dashboard.stripe.com/apikeys\n"
                "Add to .env: STRIPE_SECRET_KEY=sk_test_..."
            )
        try:
            import stripe
            stripe.api_key = self.secret_key
            self.stripe = stripe
        except ImportError:
            raise ImportError("Run: pip install stripe")

    def create_customer(self, email: str, name: str, creator_id: str) -> str:
        """
        Create a Stripe customer for a new creator.
        Returns the Stripe customer ID — save this to your DB.
        """
        customer = self.stripe.Customer.create(
            email=email,
            name=name,
            metadata={"synthcast_creator_id": creator_id},
        )
        return customer.id

    def create_checkout_session(
        self,
        customer_id: str,
        tier: CreatorTier,
        success_url: str,
        cancel_url: str,
        trial_days: int = 0,
    ) -> str:
        """
        Create a Stripe Checkout session.
        Returns the checkout URL — redirect the creator to this.

        trial_days: set to 90 for your beta creator free trial.
        """
        config = TIER_CONFIGS[tier]
        if not config.stripe_price_id:
            raise ValueError(f"No Stripe price ID configured for {tier.value} tier.")

        session_params = {
            "customer": customer_id,
            "payment_method_types": ["card"],
            "line_items": [{"price": config.stripe_price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {"tier": tier.value},
        }

        if trial_days > 0:
            session_params["subscription_data"] = {
                "trial_period_days": trial_days,
            }

        session = self.stripe.checkout.Session.create(**session_params)
        return session.url

    def create_portal_session(self, customer_id: str, return_url: str) -> str:
        """
        Create a Stripe Customer Portal session.
        Lets creators manage their subscription, update card, cancel, etc.
        Returns the portal URL.
        """
        session = self.stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url

    def get_subscription(self, subscription_id: str) -> dict:
        """Get full subscription details from Stripe."""
        sub = self.stripe.Subscription.retrieve(subscription_id)
        return {
            "id": sub.id,
            "status": sub.status,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "trial_end": sub.trial_end,
        }

    def cancel_subscription(self, subscription_id: str, immediately: bool = False) -> dict:
        """
        Cancel a subscription.
        immediately=False: cancels at period end (creator keeps access until paid through)
        immediately=True: cancels right now
        """
        if immediately:
            sub = self.stripe.Subscription.delete(subscription_id)
        else:
            sub = self.stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True,
            )
        return {"status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end}

    def verify_webhook(self, payload: bytes, sig_header: str) -> dict:
        """
        Verify a Stripe webhook signature and return the event.
        Always verify — never trust unverified webhook data.
        """
        webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET not set in .env")

        event = self.stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
        return event


# ── WEBHOOK HANDLER ───────────────────────────────────────────────────────────

class WebhookHandler:
    """
    Processes Stripe webhook events and updates creator accounts.

    Events handled:
      checkout.session.completed    → activate subscription, upgrade tier
      customer.subscription.updated → handle tier changes
      customer.subscription.deleted → downgrade to free
      invoice.payment_failed        → notify creator, grace period
      invoice.payment_succeeded     → confirm active status
    """

    def __init__(self, account_store):
        self.store = account_store

    def handle(self, event: dict) -> dict:
        event_type = event.get("type")
        data = event.get("data", {}).get("object", {})

        handlers = {
            "checkout.session.completed":    self._on_checkout_complete,
            "customer.subscription.updated": self._on_subscription_updated,
            "customer.subscription.deleted": self._on_subscription_deleted,
            "invoice.payment_failed":        self._on_payment_failed,
            "invoice.payment_succeeded":     self._on_payment_succeeded,
        }

        handler = handlers.get(event_type)
        if handler:
            return handler(data)

        return {"status": "ignored", "event_type": event_type}

    def _on_checkout_complete(self, data: dict) -> dict:
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")
        tier_name = data.get("metadata", {}).get("tier", "creator")

        account = self.store.get_by_stripe_customer(customer_id)
        if account:
            account.stripe_subscription_id = subscription_id
            account.tier = CreatorTier(tier_name)
            account.is_active = True
            self.store.save(account)
            print(f"[Billing] {account.email} upgraded to {tier_name}")

        return {"status": "processed", "event": "checkout_complete", "tier": tier_name}

    def _on_subscription_updated(self, data: dict) -> dict:
        customer_id = data.get("customer")
        status = data.get("status")

        account = self.store.get_by_stripe_customer(customer_id)
        if account:
            if status in ("active", "trialing"):
                account.is_active = True
            elif status in ("past_due", "unpaid"):
                account.is_active = False
            self.store.save(account)
            print(f"[Billing] {account.email} subscription status: {status}")

        return {"status": "processed", "event": "subscription_updated"}

    def _on_subscription_deleted(self, data: dict) -> dict:
        customer_id = data.get("customer")

        account = self.store.get_by_stripe_customer(customer_id)
        if account:
            account.tier = CreatorTier.FREE
            account.stripe_subscription_id = None
            account.is_active = True   # still active, just on free tier
            self.store.save(account)
            print(f"[Billing] {account.email} downgraded to free tier")

        return {"status": "processed", "event": "subscription_deleted"}

    def _on_payment_failed(self, data: dict) -> dict:
        customer_id = data.get("customer")
        account = self.store.get_by_stripe_customer(customer_id)
        if account:
            print(f"[Billing] Payment failed for {account.email} — send reminder email")
            # TODO: trigger email via ConvertKit or SendGrid
        return {"status": "processed", "event": "payment_failed"}

    def _on_payment_succeeded(self, data: dict) -> dict:
        customer_id = data.get("customer")
        account = self.store.get_by_stripe_customer(customer_id)
        if account:
            account.is_active = True
            self.store.save(account)
        return {"status": "processed", "event": "payment_succeeded"}


# ── IN-MEMORY ACCOUNT STORE (Phase 1) ────────────────────────────────────────

class InMemoryAccountStore:
    """
    Simple dict-based account store for MVP.
    Phase 2: swap to PostgreSQL via SQLAlchemy.
    Same interface — no other code changes needed.
    """

    def __init__(self):
        self._by_id: dict[str, CreatorAccount] = {}
        self._by_stripe: dict[str, str] = {}   # stripe_customer_id → creator_id
        self._by_email: dict[str, str] = {}     # email → creator_id

    def create(self, creator_id: str, email: str, name: str = "") -> CreatorAccount:
        account = CreatorAccount(creator_id=creator_id, email=email)
        self._by_id[creator_id] = account
        self._by_email[email.lower()] = creator_id
        return account

    def get(self, creator_id: str) -> Optional[CreatorAccount]:
        return self._by_id.get(creator_id)

    def get_by_email(self, email: str) -> Optional[CreatorAccount]:
        creator_id = self._by_email.get(email.lower())
        return self._by_id.get(creator_id) if creator_id else None

    def get_by_stripe_customer(self, stripe_customer_id: str) -> Optional[CreatorAccount]:
        creator_id = self._by_stripe.get(stripe_customer_id)
        return self._by_id.get(creator_id) if creator_id else None

    def save(self, account: CreatorAccount):
        self._by_id[account.creator_id] = account
        if account.stripe_customer_id:
            self._by_stripe[account.stripe_customer_id] = account.creator_id
        self._by_email[account.email.lower()] = account.creator_id

    def all_accounts(self) -> list[CreatorAccount]:
        return list(self._by_id.values())

    def stats(self) -> dict:
        accounts = self.all_accounts()
        return {
            "total": len(accounts),
            "free": sum(1 for a in accounts if a.tier == CreatorTier.FREE),
            "creator": sum(1 for a in accounts if a.tier == CreatorTier.CREATOR),
            "pro": sum(1 for a in accounts if a.tier == CreatorTier.PRO),
            "active": sum(1 for a in accounts if a.is_active),
            "mrr": sum(TIER_CONFIGS[a.tier].price_monthly for a in accounts if a.is_active),
        }
