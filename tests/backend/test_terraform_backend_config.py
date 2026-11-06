import pytest

from backend.app.services.terraform.backend_config import (
    build_s3_backend_config,
)


def test_build_s3_backend_config():
    config = build_s3_backend_config(123, "tf-state-techdemo")

    assert config == {
        "bucket": "tf-state-techdemo",
        "key": "training/requests/123/terraform.tfstate",
        "region": "ap-south-1",
        "encrypt": True,
        "use_lockfile": True,
    }


def test_each_request_gets_a_unique_state_key():
    first = build_s3_backend_config(123, "tf-state-techdemo")
    second = build_s3_backend_config(456, "tf-state-techdemo")

    assert first["key"] != second["key"]


@pytest.mark.parametrize("request_id", [0, -1, True, False])
def test_rejects_invalid_request_id(request_id):
    with pytest.raises(ValueError):
        build_s3_backend_config(request_id, "tf-state-techdemo")


@pytest.mark.parametrize(
    ("bucket", "region"),
    [("", "ap-south-1"), ("tf-state-techdemo", "")],
)
def test_rejects_missing_bucket_or_region(bucket, region):
    with pytest.raises(ValueError):
        build_s3_backend_config(123, bucket, region)
