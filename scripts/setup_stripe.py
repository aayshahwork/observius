#!/usr/bin/env python3
"""
scripts/setup_stripe.py — Create Stripe products and prices for the pokant tier system.

Usage:
    STRIPE_SECRET_KEY=sk_test_... python scripts/setup_stripe.py

Creates three products (Startup, Growth, Enterprise) with recurring monthly prices,
then prints the STRIPE_PRICE_IDS env var ready to paste into .env.
"""

from __future__ import annotations

import json
import os
import sys

try:
    import stripe
except ImportError:
    print("ERROR: stripe package not installed. Run: pip install stripe")
    sys.exit(1)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
if not stripe.api_key:
    print("ERROR: Set STRIPE_SECRET_KEY environment variable.")
    sys.exit(1)

# Tier definitions matching shared/constants.py
TIERS = [
    {
        "name": "Startup",
        "tier_key": "startup",
        "price_cents": 2900,  # $29/month
        "step_limit": 5_000,
        "description": "5,000 steps/month, 5 concurrent tasks, 200 max steps/task",
    },
    {
        "name": "Growth",
        "tier_key": "growth",
        "price_cents": 9900,  # $99/month
        "step_limit": 25_000,
        "description": "25,000 steps/month, 10 concurrent tasks, 350 max steps/task",
    },
    {
        "name": "Enterprise",
        "tier_key": "enterprise",
        "price_cents": 29900,  # $299/month
        "step_limit": 100_000,
        "description": "100,000 steps/month, 20 concurrent tasks, 500 max steps/task",
    },
]


def main() -> None:
    print("Creating Stripe products and prices...\n")

    price_ids: dict[str, str] = {}

    for tier in TIERS:
        # Check for existing product with this metadata to avoid duplicates
        existing = stripe.Product.search(
            query=f"metadata['pokant_tier']:'{tier['tier_key']}'",
        )
        if existing.data:
            product = existing.data[0]
            print(f"  Found existing product: {product.name} ({product.id})")
        else:
            product = stripe.Product.create(
                name=f"Pokant {tier['name']}",
                description=tier["description"],
                metadata={
                    "pokant_tier": tier["tier_key"],
                    "monthly_step_limit": str(tier["step_limit"]),
                },
            )
            print(f"  Created product: {product.name} ({product.id})")

        # Check for existing price on this product
        existing_prices = stripe.Price.list(product=product.id, active=True, limit=5)
        matching_price = None
        for p in existing_prices.data:
            if (
                p.unit_amount == tier["price_cents"]
                and p.recurring
                and p.recurring.interval == "month"
            ):
                matching_price = p
                break

        if matching_price:
            price = matching_price
            print(f"  Found existing price: ${tier['price_cents']/100:.0f}/mo ({price.id})")
        else:
            price = stripe.Price.create(
                product=product.id,
                unit_amount=tier["price_cents"],
                currency="usd",
                recurring={"interval": "month"},
                metadata={"pokant_tier": tier["tier_key"]},
            )
            print(f"  Created price: ${tier['price_cents']/100:.0f}/mo ({price.id})")

        price_ids[tier["tier_key"]] = price.id
        print()

    # Print results
    print("=" * 60)
    print("  STRIPE PRICE IDS")
    print("=" * 60)
    print()
    for tier_key, price_id in price_ids.items():
        print(f"  {tier_key:15s} = {price_id}")
    print()
    print("Add to .env:")
    print()
    env_value = json.dumps(price_ids)
    print(f'STRIPE_PRICE_IDS={env_value}')
    print()


if __name__ == "__main__":
    main()
