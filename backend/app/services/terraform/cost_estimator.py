
"""Transparent, assumption-based infrastructure cost estimates."""

from decimal import Decimal, InvalidOperation
from typing import Any


HOURS_PER_MONTH = Decimal("730")


def estimate_ec2_monthly_cost(
    instance_type: str,
    region: str,
    pricing_catalog: dict[str, Any],
    budget_threshold: Decimal | int | float | str | None = None,
) -> dict[str, Any]:
    """Estimate EC2 compute cost using a trusted regional pricing catalog.

    Expected catalog format:
        {
            "ap-south-1": {
                "t3.medium": {
                    "hourly_usd": "0.05",
                    "source": "Verified pricing source",
                    "verified_on": "2026-10-27"
                }
            }
        }

    The example above illustrates the schema only; it is not a
    verified AWS price. Do not insert example rates into production.
    """
    if not isinstance(pricing_catalog, dict):
        raise ValueError("Pricing catalog must be a dictionary.")

    region_prices = pricing_catalog.get(region, {})
    if not isinstance(region_prices, dict):
        raise ValueError("Regional pricing entries must be a dictionary.")

    entry = region_prices.get(instance_type)

    if not isinstance(entry, dict):
        return {
            "status": "unavailable",
            "currency": "USD",
            "instance_type": instance_type,
            "region": region,
            "estimated_monthly_compute_cost": None,
            "budget_threshold": (
                str(budget_threshold)
                if budget_threshold is not None
                else None
            ),
            "budget_status": "not_evaluated",
            "assumptions": {
                "hours_per_month": str(HOURS_PER_MONTH),
            },
            "note": (
                "No verified hourly price is available for this "
                "instance type and region."
            ),
        }

    try:
        hourly_rate = Decimal(str(entry["hourly_usd"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Pricing entry has an invalid hourly rate.") from exc

    if not hourly_rate.is_finite() or hourly_rate < 0:
        raise ValueError("Hourly rate must be a finite, non-negative number.")

    if not entry.get("source") or not entry.get("verified_on"):
        raise ValueError(
            "Pricing entry must include its source and verification date."
        )

    monthly_compute_cost = (hourly_rate * HOURS_PER_MONTH).quantize(
        Decimal("0.01")
    )

    threshold = None
    if budget_threshold is not None:
        try:
            threshold = Decimal(str(budget_threshold))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Budget threshold must be a valid number.") from exc

        if not threshold.is_finite() or threshold < 0:
            raise ValueError(
                "Budget threshold must be a finite, non-negative number."
            )

    if threshold is None:
        budget_status = "not_configured"
    elif monthly_compute_cost > threshold:
        budget_status = "exceeds_threshold"
    else:
        budget_status = "within_threshold"

    return {
        "status": "estimated",
        "currency": "USD",
        "instance_type": instance_type,
        "region": region,
        "estimated_monthly_compute_cost": str(monthly_compute_cost),
        "budget_threshold": str(threshold) if threshold is not None else None,
        "budget_status": budget_status,
        "pricing_source": entry["source"],
        "pricing_verified_on": entry["verified_on"],
        "assumptions": {
            "hours_per_month": str(HOURS_PER_MONTH),
            "hourly_rate_usd": str(hourly_rate),
            "includes": ["EC2 compute only"],
            "excludes": [
                "EBS storage",
                "Public IPv4 charges",
                "Data transfer",
                "Taxes",
                "Other AWS services",
            ],
        },
        "note": (
            "This is an estimate, not an AWS bill or a complete "
            "infrastructure cost forecast."
        ),
    }
