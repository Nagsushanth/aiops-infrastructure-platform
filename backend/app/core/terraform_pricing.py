"""Trusted server-side configuration for Terraform cost reviews."""

from decimal import Decimal
from typing import Any

PRICING_CATALOG: dict[str, Any] = {
    "ap-south-1": {
        "t3.small": {
            "hourly_usd": "0.0224",
            "source": "AWS Price List API - AmazonEC2 On-Demand",
            "verified_on": "2026-10-01",
        }
    }
}

# Keep unset until the project budget is confirmed.
BUDGET_THRESHOLD_USD: Decimal | None = None