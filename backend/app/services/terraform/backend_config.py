"""Build request-specific Terraform S3 backend configuration."""

from __future__ import annotations

import re
from typing import Any


def build_s3_backend_config(
    infrastructure_request_id: int,
    bucket: str,
    region: str = "ap-south-1",
    prefix: str = "training/requests",
) -> dict[str, Any]:
    """Return an isolated S3 backend configuration for one request."""

    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise ValueError("Infrastructure request ID must be a positive integer.")

    bucket = bucket.strip()
    region = region.strip()
    prefix = prefix.strip("/")

    if not bucket:
        raise ValueError("S3 state bucket must not be empty.")

    if not region:
        raise ValueError("AWS region must not be empty.")

    if not prefix or not re.fullmatch(r"[A-Za-z0-9/_-]+", prefix):
        raise ValueError("S3 state prefix contains invalid characters.")

    return {
        "bucket": bucket,
        "key": (
            f"{prefix}/{infrastructure_request_id}/terraform.tfstate"
        ),
        "region": region,
        "encrypt": True,
        "use_lockfile": True,
    }
