"""
synthcast/billing/routes.py

FastAPI billing routes.
Mount these into the main API server.

Endpoints:
  POST /billing/register          — create creator account
  POST /billing/subscribe         — start Stripe checkout
  POST /billing/portal            — manage subscription
  GET  /billing/account/{id}      — get account + tier info
  POST /billing/webhook           — Stripe webhook receiver
  GET  /billing/stats             — MRR and tier breakdown
  POST /billing/beta-access       — grant free beta creator access
"""

import os
import asyncio
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel, EmailStr
from typing import Optional

from .stripe_billing import (
    StripeClient, WebhookHandler, InMemoryAccountStore,
    CreatorAccount, CreatorTier, TIER_CONFIGS
)

router = APIRouter(prefix="/billing", tags=["billing"])

# Shared state — in production, inject via dependency
account_store = InMemoryAccountStore()
stripe_client: Optional[StripeClient] = None
webhook_handler = WebhookHandler(account_store)


def get_stripe() -> StripeClient:
    global stripe_client
    if not stripe_client:
        key = os.getenv("STRIPE_SECRET_KEY")
        if not key:
            raise HTTPException(
                400,
                "Stripe not configured. Add STRIPE_SECRET_KEY to .env"
            )
        stripe_client = StripeClient(key)
    return stripe_client


# ── REQUEST MODELS ────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    creator_id: str
    email: str
    name: str


class SubscribeRequest(BaseModel):
    creator_id: str
    tier: str   # "creator" or "pro"
    success_url: str = "https://synthcast.io/dashboard?subscribed=true"
    cancel_url: str  = "https://synthcast.io/pricing"
    trial_days: int  = 0


class BetaAccessRequest(BaseModel):
    creator_id: str
    email: str
    name: str
    tier: str = "creator"
    trial_days: int = 90


# ── ENDPOINTS ─────────────────────────────────────────────────────────────────

@router.post("/register")
async def register_creator(req: RegisterRequest):
    """
    Register a new creator account.
    Call this when a creator signs up on the Synthcast landing page.
    """
    existing = account_store.get_by_email(req.email)
    if existing:
        return existing.to_dict()

    account = account_store.create(
        creator_id=req.creator_id,
        email=req.email,
        name=req.name,
    )

    # Create Stripe customer (non-blocking)
    loop = asyncio.get_event_loop()
    try:
        stripe = get_stripe()
        customer_id = await loop.run_in_executor(
            None,
            lambda: stripe.create_customer(req.email, req.name, req.creator_id)
        )
        account.stripe_customer_id = customer_id
        account_store.save(account)
    except Exception as e:
        # Stripe not configured yet — account still created, just no Stripe ID
        print(f"[Billing] Stripe customer creation skipped: {e}")

    return account.to_dict()


@router.post("/subscribe")
async def start_subscription(req: SubscribeRequest):
    """
    Start a Stripe Checkout session for a creator to subscribe.
    Returns a checkout URL — redirect the creator to this URL.
    """
    account = account_store.get(req.creator_id)
    if not account:
        raise HTTPException(404, "Creator not found. Call /billing/register first.")

    if not account.stripe_customer_id:
        raise HTTPException(400, "No Stripe customer ID. Call /billing/register first.")

    try:
        tier = CreatorTier(req.tier)
    except ValueError:
        raise HTTPException(400, f"Invalid tier '{req.tier}'. Choose: creator, pro")

    stripe = get_stripe()
    loop = asyncio.get_event_loop()

    checkout_url = await loop.run_in_executor(
        None,
        lambda: stripe.create_checkout_session(
            customer_id=account.stripe_customer_id,
            tier=tier,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
            trial_days=req.trial_days,
        )
    )

    return {
        "checkout_url": checkout_url,
        "tier": req.tier,
        "price": TIER_CONFIGS[tier].price_monthly,
    }


@router.post("/portal")
async def billing_portal(creator_id: str, return_url: str = "https://synthcast.io/dashboard"):
    """
    Open Stripe Customer Portal for a creator.
    They can update card, view invoices, cancel subscription.
    Returns portal URL — redirect creator here.
    """
    account = account_store.get(creator_id)
    if not account or not account.stripe_customer_id:
        raise HTTPException(404, "Creator not found or no billing setup.")

    stripe = get_stripe()
    loop = asyncio.get_event_loop()

    portal_url = await loop.run_in_executor(
        None,
        lambda: stripe.create_portal_session(account.stripe_customer_id, return_url)
    )

    return {"portal_url": portal_url}


@router.get("/account/{creator_id}")
async def get_account(creator_id: str):
    """Get a creator's account, tier, and feature access."""
    account = account_store.get(creator_id)
    if not account:
        raise HTTPException(404, "Creator not found.")
    return account.to_dict()


@router.get("/tiers")
async def get_tiers():
    """Return all tier configurations and pricing."""
    return {
        tier.value: {
            "name": config.name,
            "price_monthly": config.price_monthly,
            "platforms": config.platforms,
            "session_limit_hrs": config.session_limit_hrs,
            "voice_clone": config.voice_clone,
            "video_avatar": config.video_avatar,
            "analytics": config.analytics,
            "brand_deals": config.brand_deals,
            "white_label": config.white_label,
        }
        for tier, config in TIER_CONFIGS.items()
    }


@router.post("/beta-access")
async def grant_beta_access(req: BetaAccessRequest):
    """
    Grant a beta creator free access for 90 days.
    Use this for your first 50 hand-picked creators.
    No Stripe checkout needed — sets tier directly.
    """
    account = account_store.get_by_email(req.email)
    if not account:
        account = account_store.create(
            creator_id=req.creator_id,
            email=req.email,
            name=req.name,
        )

    try:
        account.tier = CreatorTier(req.tier)
    except ValueError:
        account.tier = CreatorTier.CREATOR

    account.is_active = True

    import time
    account.trial_ends_at = time.time() + (req.trial_days * 86400)
    account_store.save(account)

    print(f"[Billing] Beta access granted: {req.email} → {req.tier} for {req.trial_days} days")

    return {
        "status": "granted",
        "email": req.email,
        "tier": account.tier.value,
        "trial_days": req.trial_days,
        "features": {
            "platforms": account.config.platforms,
            "voice_clone": account.config.voice_clone,
            "analytics": account.config.analytics,
        }
    }


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
):
    """
    Stripe webhook endpoint.
    Add this URL in your Stripe dashboard:
      https://synthcast.io/billing/webhook

    Events to enable in Stripe dashboard:
      - checkout.session.completed
      - customer.subscription.updated
      - customer.subscription.deleted
      - invoice.payment_failed
      - invoice.payment_succeeded
    """
    payload = await request.body()

    if not stripe_signature:
        raise HTTPException(400, "Missing stripe-signature header")

    try:
        stripe = get_stripe()
        event = stripe.verify_webhook(payload, stripe_signature)
    except Exception as e:
        raise HTTPException(400, f"Webhook verification failed: {e}")

    result = webhook_handler.handle(event)
    return result


@router.get("/stats")
async def billing_stats():
    """MRR, tier breakdown, and account stats."""
    return account_store.stats()
