
import json

import pytest

from backend.app.services.terraform.aws_pricing import (
    AwsPricingError,
    clear_pricing_cache,
    get_ec2_pricing_entry,
)


@pytest.fixture(autouse=True)
def clear_cache():
    clear_pricing_cache()
    yield
    clear_pricing_cache()


def make_price_document(price="0.0208"):
    return json.dumps(
        {
            "terms": {
                "OnDemand": {
                    "term-1": {
                        "priceDimensions": {
                            "dimension-1": {
                                "unit": "Hrs",
                                "pricePerUnit": {"USD": price},
                            }
                        }
                    }
                }
            }
        }
    )


class FakePricingClient:
    def __init__(self, response=None, error=None):
        self.response = response or {
            "PriceList": [make_price_document()]
        }
        self.error = error
        self.calls = []

    def get_products(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_retrieves_ec2_on_demand_hourly_price():
    client = FakePricingClient()

    result = get_ec2_pricing_entry(
        "t3.small",
        "ap-south-1",
        pricing_client=client,
    )

    assert result["hourly_usd"] == "0.0208"
    assert result["source"] == (
        "AWS Price List API - AmazonEC2 On-Demand"
    )
    assert result["verified_on"]
    assert len(client.calls) == 1


def test_uses_correct_ec2_product_filters():
    client = FakePricingClient()

    get_ec2_pricing_entry(
        "t3.small",
        "ap-south-1",
        pricing_client=client,
    )

    filters = {
        item["Field"]: item["Value"]
        for item in client.calls[0]["Filters"]
    }

    assert filters["instanceType"] == "t3.small"
    assert filters["regionCode"] == "ap-south-1"
    assert filters["operatingSystem"] == "Linux"
    assert filters["tenancy"] == "Shared"
    assert filters["preInstalledSw"] == "NA"
    assert filters["capacitystatus"] == "Used"
    assert filters["licenseModel"] == "No License required"


def test_caches_pricing_response():
    client = FakePricingClient()

    first = get_ec2_pricing_entry(
        "t3.small",
        "ap-south-1",
        pricing_client=client,
    )

    second = get_ec2_pricing_entry(
        "t3.small",
        "ap-south-1",
        pricing_client=client,
    )

    assert first == second
    assert len(client.calls) == 1


def test_missing_price_raises_safe_error():
    client = FakePricingClient(response={"PriceList": []})

    with pytest.raises(
        AwsPricingError,
        match="No hourly USD On-Demand price",
    ):
        get_ec2_pricing_entry(
            "t3.small",
            "ap-south-1",
            pricing_client=client,
        )


def test_ambiguous_prices_are_rejected():
    client = FakePricingClient(
        response={
            "PriceList": [
                make_price_document("0.0208"),
                make_price_document("0.0300"),
            ]
        }
    )

    with pytest.raises(AwsPricingError, match="ambiguous"):
        get_ec2_pricing_entry(
            "t3.small",
            "ap-south-1",
            pricing_client=client,
        )


def test_invalid_instance_type_is_rejected():
    with pytest.raises(ValueError):
        get_ec2_pricing_entry(
            "",
            "ap-south-1",
            pricing_client=FakePricingClient(),
        )


def test_different_instance_types_use_separate_cache_entries():
    client = FakePricingClient()

    get_ec2_pricing_entry(
        "t3.small",
        "ap-south-1",
        pricing_client=client,
    )

    get_ec2_pricing_entry(
        "t3.medium",
        "ap-south-1",
        pricing_client=client,
    )

    assert len(client.calls) == 2
