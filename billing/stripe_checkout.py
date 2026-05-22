"""
billing/stripe_checkout.py
Stripe checkout flow for Synthcast creator upgrades.

Endpoints:
  POST /billing/checkout        — create Stripe checkout session
  POST /billing/webhook         — handle Stripe webhook events
  GET  /billing/success         — success page after checkout
  GET  /billing/cancel          — cancel page
"""

import os
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/billing", tags=["billing"])

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
APP_URL = os.getenv("APP_URL", "https://synthcast.live")
API_URL = os.getenv("API_URL", "https://synthcast-production.up.railway.app")

# Stripe Price IDs — set these in Railway after creating products in Stripe
PRICE_IDS = {
    "creator": os.getenv("STRIPE_CREATOR_PRICE_ID", ""),
    "pro": os.getenv("STRIPE_PRO_PRICE_ID", ""),
}


class CheckoutRequest(BaseModel):
    creator_id: str
    tier: str  # "creator" or "pro"
    email: str
    name: Optional[str] = ""


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest):
    """Create a Stripe checkout session and return the URL."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(500, "Stripe not configured.")

    price_id = PRICE_IDS.get(req.tier)
    if not price_id:
        raise HTTPException(400, f"No price configured for tier: {req.tier}. Add STRIPE_{req.tier.upper()}_PRICE_ID to Railway.")

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        # Find or create customer
        customers = stripe.Customer.list(email=req.email, limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=req.email,
                name=req.name or req.email.split("@")[0],
                metadata={"creator_id": req.creator_id}
            )

        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=["card"],
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=f"{APP_URL}/app?upgraded=true&tier={req.tier}",
            cancel_url=f"{APP_URL}/app?upgrade_cancelled=true",
            metadata={
                "creator_id": req.creator_id,
                "tier": req.tier,
            },
            allow_promotion_codes=True,
            billing_address_collection="auto",
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
        }

    except Exception as e:
        raise HTTPException(500, f"Stripe error: {str(e)}")


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY

        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            import json
            event = json.loads(payload)

    except Exception as e:
        raise HTTPException(400, f"Webhook error: {str(e)}")

    event_type = event.get("type", "")

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        creator_id = session.get("metadata", {}).get("creator_id")
        tier = session.get("metadata", {}).get("tier", "creator")

        if creator_id:
            _upgrade_creator(creator_id, tier)
            print(f"[Stripe] Upgraded creator {creator_id} to {tier}")

    elif event_type == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            _downgrade_creator_by_stripe_id(customer_id)
            print(f"[Stripe] Downgraded customer {customer_id}")

    elif event_type == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")
        print(f"[Stripe] Payment failed for customer {customer_id}")

    return {"status": "ok"}


def _upgrade_creator(creator_id: str, tier: str):
    """Upgrade a creator's tier after successful payment."""
    try:
        from billing.auth_routes import _users, _get_user_by_id
        user = _get_user_by_id(creator_id)
        if user:
            user["tier"] = tier
            print(f"[Stripe] Auth tier updated: {creator_id} → {tier}")
    except Exception as e:
        print(f"[Stripe] Auth upgrade failed: {e}")

    try:
        from billing.routes import account_store
        account = account_store.get(creator_id)
        if account:
            from billing.stripe_billing import CreatorTier
            account.tier = CreatorTier(tier)
            account_store.save(account)
            print(f"[Stripe] Billing tier updated: {creator_id} → {tier}")
    except Exception as e:
        print(f"[Stripe] Billing upgrade failed: {e}")


def _downgrade_creator_by_stripe_id(stripe_customer_id: str):
    """Downgrade a creator when subscription is cancelled."""
    try:
        from billing.routes import account_store
        accounts = account_store.list_all()
        for account in accounts:
            if account.stripe_customer_id == stripe_customer_id:
                from billing.stripe_billing import CreatorTier
                account.tier = CreatorTier.FREE
                account_store.save(account)
                print(f"[Stripe] Downgraded account to free: {account.creator_id}")
                break
    except Exception as e:
        print(f"[Stripe] Downgrade failed: {e}")


@router.get("/success", response_class=HTMLResponse, include_in_schema=False)
async def checkout_success():
    """Redirect to app after successful checkout."""
    return RedirectResponse(f"{APP_URL}/app?upgraded=true")


@router.get("/cancel", response_class=HTMLResponse, include_in_schema=False)
async def checkout_cancel():
    """Redirect to app after cancelled checkout."""
    return RedirectResponse(f"{APP_URL}/app")
