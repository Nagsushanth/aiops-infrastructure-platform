
from decimal import Decimal

import pytest

from backend.app.services.terraform.cost_estimator import (
    estimate_ec2_monthly_cost,
)


TEST_CATALOG = {
    "ap-south-1": {
        "t3.medium": {
            "hourly_usd": "0.10",
            "source": "Test fixture only",
            "verified_on": "2026-10-27",
        }
    }
}


def test_estimates_monthly_compute_cost():
    result = estimate_ec2_monthly_cost(
        "t3.medium",
        "ap-south-1",
        TEST_CATALOG,
    )

    assert result["status"] == "estimated"
    assert result["estimated_monthly_compute_cost"] == "73.00"
    assert result["budget_status"] == "not_configured"


def test_flags_cost_above_budget_threshold():
    result = estimate_ec2_monthly_cost(
        "t3.medium",
        "ap-south-1",
        TEST_CATALOG,
        budget_threshold=50,
    )

    assert result["budget_status"] == "exceeds_threshold"


def test_cost_within_budget_threshold():
    result = estimate_ec2_monthly_cost(
        "t3.medium",
        "ap-south-1",
        TEST_CATALOG,
        budget_threshold=100,
    )

    assert result["budget_status"] == "within_threshold"


def test_missing_pricing_returns_unavailable():
    result = estimate_ec2_monthly_cost(
        "t3.medium",
        "eu-west-1",
        TEST_CATALOG,
    )

    assert result["status"] == "unavailable"
    assert result["estimated_monthly_compute_cost"] is None
    assert result["budget_status"] == "not_evaluated"


def test_missing_pricing_provenance_is_rejected():
    catalog = {
        "ap-south-1": {
            "t3.medium": {"hourly_usd": "0.10"}
        }
    }

    with pytest.raises(ValueError):
        estimate_ec2_monthly_cost(
            "t3.medium", "ap-south-1", catalog
        )


@pytest.mark.parametrize("rate", ["-1", "NaN", "Infinity", "invalid"])
def test_rejects_invalid_hourly_rates(rate):
    catalog = {
        "ap-south-1": {
            "t3.medium": {
                "hourly_usd": rate,
                "source": "Test fixture",
                "verified_on": "2026-10-27",
            }
        }
    }

    with pytest.raises(ValueError):
        estimate_ec2_monthly_cost(
            "t3.medium", "ap-south-1", catalog
        )


def test_exact_budget_threshold_is_within_budget():
    catalog = {
        "ap-south-1": {
            "t3.medium": {
                "hourly_usd": "0.10",
                "source": "Test fixture",
                "verified_on": "2026-10-27",
            }
        }
    }

    result = estimate_ec2_monthly_cost(
        "t3.medium",
        "ap-south-1",
        catalog,
        budget_threshold=Decimal("73.00"),
    )

    assert result["budget_status"] == "within_threshold"
