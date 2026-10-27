
"""Trusted server-side configuration for Terraform cost reviews.

Keep this catalog empty until regional rates have been verified.
Never populate it with example or test-only prices.
"""

from decimal import Decimal
from typing import Any

# Shape:
# {
#     "ap-south-1": {
#         "verified-instance-type": {
#             "hourly_usd": "verified rate",
#             "source": "official pricing source",
#             "verified_on": "YYYY-MM-DD",
#         }
#     }
# }
#
# These entries must be verified before being added.
PRICING_CATALOG: dict[str, Any] = {}

# None means no server-configured budget threshold.
# Configure this from approved application settings when available.
BUDGET_THRESHOLD_USD: Decimal | None = None
