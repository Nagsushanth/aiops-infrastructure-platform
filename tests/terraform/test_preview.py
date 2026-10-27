
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.core.dependencies import get_db
from backend.app.core.security import create_access_token
from backend.app.models.base import Base
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.models.user import User
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import TerraformGenerationError
from backend.app.services.terraform.preview import build_plan_preview


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False,
)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def auth_headers(client):
    db = TestingSessionLocal()
    try:
        user = User(
            email="preview-test@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "Authorization": (
                f"Bearer {create_access_token(str(user.id))}"
            )
        }
    finally:
        db.close()


def make_spec(**overrides):
    values = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 4,
        "memory_gb": 8,
        "public_access": False,
        "operating_system": "linux",
    }
    values.update(overrides)
    return ValidatedInfrastructureSpecification(**values)


def test_preview_describes_supported_ec2_resources():
    preview = build_plan_preview(make_spec())

    assert preview["preview_type"] == "local_proposed_resource_preview"
    assert preview["is_real_terraform_plan"] is False
    assert preview["deployment_enabled"] is False
    assert preview["status"] == "ready_for_review"
    assert preview["resource_summary"]["create"] == 2
    assert preview["resource_summary"]["update"] == 0
    assert preview["resource_summary"]["delete"] == 0


def test_preview_reports_security_defaults():
    preview = build_plan_preview(make_spec())
    configuration = preview["configuration"]

    assert configuration["inbound_security_group_rules"] == 0
    assert configuration["root_volume_encrypted"] is True
    assert configuration["imds_v2_required"] is True
    assert configuration["public_ip_associated"] is False


def test_preview_does_not_invent_cost_estimate():
    preview = build_plan_preview(make_spec())

    assert preview["estimated_monthly_cost"] is None
    assert preview["approval"]["technical_review"] == "pending"
    assert preview["approval"]["cost_review"] == "pending"
    assert preview["approval"]["security_review"] == "pending"


def test_preview_rejects_unsupported_cpu_memory_combination():
    with pytest.raises(TerraformGenerationError):
        build_plan_preview(make_spec(vcpus=5, memory_gb=8))


def test_preview_includes_generated_terraform():
    preview = build_plan_preview(make_spec())

    assert 'resource "aws_instance" "this"' in preview["terraform_configuration"]
    assert 'resource "aws_security_group" "instance"' in preview["terraform_configuration"]


def test_preview_api_rejects_missing_token(client):
    response = client.post(
        "/terraform/preview",
        json=make_spec().model_dump(),
    )

    assert response.status_code == 401


def test_preview_api_rejects_invalid_token(client):
    response = client.post(
        "/terraform/preview",
        headers={"Authorization": "Bearer invalid-token"},
        json=make_spec().model_dump(),
    )

    assert response.status_code == 401


def test_preview_api_accepts_authenticated_user(client, auth_headers):
    response = client.post(
        "/terraform/preview",
        headers=auth_headers,
        json=make_spec().model_dump(),
    )

    assert response.status_code == 200
    assert response.json()["deployment_enabled"] is False
    assert response.json()["preview_type"] == "local_proposed_resource_preview"


def test_preview_api_rejects_unsupported_specification(client, auth_headers):
    invalid_spec = {
        **make_spec().model_dump(),
        "vcpus": 5,
        "memory_gb": 8,
    }

    response = client.post(
        "/terraform/preview",
        headers=auth_headers,
        json=invalid_spec,
    )

    assert response.status_code == 422


def test_plan_parse_api_rejects_missing_token(client):
    response = client.post(
        "/terraform/plan/parse",
        json={"plan": {"resource_changes": []}},
    )

    assert response.status_code == 401


def test_plan_parse_api_accepts_authenticated_user(client, auth_headers):
    plan = {
        "terraform_version": "1.15.6",
        "format_version": "1.2",
        "resource_changes": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"instance_type": "c6i.xlarge"},
                },
            }
        ],
    }

    response = client.post(
        "/terraform/plan/parse",
        headers=auth_headers,
        json={"plan": plan},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preview_type"] == "terraform_plan_json"
    assert body["is_real_terraform_plan"] is False
    assert body["source_verified"] is False
    assert body["deployment_enabled"] is False
    assert body["resource_summary"]["create"] == 1
    assert body["resources"][0]["address"] == "aws_instance.this"


def test_plan_parse_api_rejects_invalid_plan_structure(
    client,
    auth_headers,
):
    response = client.post(
        "/terraform/plan/parse",
        headers=auth_headers,
        json={"plan": {"resource_changes": {}}},
    )

    assert response.status_code == 422


def test_plan_parse_api_rejects_extra_request_fields(
    client,
    auth_headers,
):
    response = client.post(
        "/terraform/plan/parse",
        headers=auth_headers,
        json={
            "plan": {"resource_changes": []},
            "unexpected": True,
        },
    )

    assert response.status_code == 422


def test_preview_cost_is_unavailable_without_verified_pricing():
    preview = build_plan_preview(make_spec())

    assert preview["estimated_monthly_cost"] is None
    assert preview["cost_estimate"]["status"] == "unavailable"
    assert preview["cost_estimate"]["budget_status"] == "not_evaluated"
    assert "no verified price" in preview["cost_estimate_note"].lower()


def test_preview_estimates_cost_from_supplied_pricing_catalog():
    # Test-only rate. This is not a verified AWS price.
    test_catalog = {
        "ap-south-1": {
            "c6i.xlarge": {
                "hourly_usd": "0.10",
                "source": "Test fixture only",
                "verified_on": "2026-10-27",
            }
        }
    }

    preview = build_plan_preview(
        make_spec(),
        pricing_catalog=test_catalog,
        budget_threshold=50,
    )

    assert preview["configuration"]["instance_type"] == "c6i.xlarge"
    assert preview["estimated_monthly_cost"] == "73.00"
    assert preview["cost_estimate"]["status"] == "estimated"
    assert preview["cost_estimate"]["budget_status"] == "exceeds_threshold"
    assert preview["cost_estimate"]["pricing_source"] == "Test fixture only"
    assert preview["deployment_enabled"] is False


def test_preview_cost_within_budget():
    test_catalog = {
        "ap-south-1": {
            "c6i.xlarge": {
                "hourly_usd": "0.10",
                "source": "Test fixture only",
                "verified_on": "2026-10-27",
            }
        }
    }

    preview = build_plan_preview(
        make_spec(),
        pricing_catalog=test_catalog,
        budget_threshold=100,
    )

    assert preview["cost_estimate"]["budget_status"] == "within_threshold"
