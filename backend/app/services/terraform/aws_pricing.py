
"""Retrieve EC2 On-Demand pricing from the AWS Price List API."""

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


PRICING_API_REGION = os.getenv(
    "AWS_PRICING_API_REGION",
    "ap-south-1",
)

PRICING_CACHE_TTL_SECONDS = 6 * 60 * 60

_pricing_cache: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_cache_lock = threading.Lock()


class AwsPricingError(RuntimeError):
    """Safe error raised when AWS pricing cannot be retrieved."""


def _create_pricing_client():
    """Create the AWS Price List client using the default AWS credential chain."""
    return boto3.client(
        "pricing",
        region_name=PRICING_API_REGION,
    )


def _extract_hourly_usd(price_list: list[str]) -> str:
    """Extract one unambiguous hourly USD On-Demand price."""

    hourly_prices = set()

    for product_json in price_list:
        try:
            product = json.loads(product_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AwsPricingError(
                "AWS returned an invalid pricing document."
            ) from exc

        terms = product.get("terms", {})
        on_demand = terms.get("OnDemand", {})

        for term in on_demand.values():
            for dimension in term.get("priceDimensions", {}).values():
                if dimension.get("unit") != "Hrs":
                    continue

                usd_price = dimension.get(
                    "pricePerUnit", {}
                ).get("USD")

                if usd_price is None:
                    continue

                try:
                    price = float(usd_price)
                except (TypeError, ValueError) as exc:
                    raise AwsPricingError(
                        "AWS returned an invalid hourly price."
                    ) from exc

                if price < 0 or not price < float("inf"):
                    raise AwsPricingError(
                        "AWS returned a non-finite hourly price."
                    )

                hourly_prices.add(usd_price)

    if not hourly_prices:
        raise AwsPricingError(
            "No hourly USD On-Demand price was returned."
        )

    if len(hourly_prices) != 1:
        raise AwsPricingError(
            "AWS returned ambiguous hourly pricing."
        )

    return hourly_prices.pop()


def get_ec2_pricing_entry(
    instance_type: str,
    region: str,
    pricing_client: Any = None,
) -> dict[str, str]:
    """Return a verified EC2 Linux shared-tenancy On-Demand price.

    The cache is process-local and expires after six hours.
    """

    if (
        not isinstance(instance_type, str)
        or not instance_type.strip()
        or not isinstance(region, str)
        or not region.strip()
    ):
        raise ValueError("Instance type and region are required.")

    cache_key = (instance_type.strip(), region.strip())
    now = time.monotonic()

    with _cache_lock:
        cached = _pricing_cache.get(cache_key)

        if cached is not None and cached[0] > now:
            return dict(cached[1])

    filters = [
        ("instanceType", cache_key[0]),
        ("regionCode", cache_key[1]),
        ("operatingSystem", "Linux"),
        ("tenancy", "Shared"),
        ("preInstalledSw", "NA"),
        ("capacitystatus", "Used"),
        ("licenseModel", "No License required"),
    ]

    try:
        client = pricing_client or _create_pricing_client()
        response = client.get_products(
            ServiceCode="AmazonEC2",
            FormatVersion="aws_v1",
            MaxResults=10,
            Filters=[
                {
                    "Type": "TERM_MATCH",
                    "Field": field,
                    "Value": value,
                }
                for field, value in filters
            ],
        )

        hourly_usd = _extract_hourly_usd(
            response.get("PriceList", [])
        )

    except (BotoCoreError, ClientError) as exc:
        raise AwsPricingError(
            "AWS Price List API request failed."
        ) from exc

    entry = {
        "hourly_usd": hourly_usd,
        "source": "AWS Price List API - AmazonEC2 On-Demand",
        "verified_on": datetime.now(timezone.utc).date().isoformat(),
    }

    with _cache_lock:
        _pricing_cache[cache_key] = (
            time.monotonic() + PRICING_CACHE_TTL_SECONDS,
            entry,
        )

    return dict(entry)


def clear_pricing_cache() -> None:
    """Clear the process-local pricing cache, primarily for tests."""

    with _cache_lock:
        _pricing_cache.clear()

def get_ec2_pricing_catalog(
    instance_type: str,
    region: str,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return a cost-estimator catalog using live AWS pricing.

    If AWS pricing is unavailable, return an empty catalog.
    Never fall back to a hardcoded or stale price.
    """
    try:
        entry = get_ec2_pricing_entry(
            instance_type=instance_type,
            region=region,
        )
    except AwsPricingError:
        return {}

    return {
        region: {
            instance_type: entry,
        }
    }