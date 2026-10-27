
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
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.models.user import User
import backend.app.api.terraform_plans as terraform_plans_api
from backend.app.services.terraform.execution import TerraformExecutionError


test_engine = create_engine(
    "sqlite://",
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

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def setup_data(client):
    db = TestingSessionLocal()
    try:
        requester = User(
            email="plan-requester@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        other_user = User(
            email="plan-other@example.com",
            password_hash="test-hash",
            role="admin",
            is_active=True,
        )
        db.add_all([requester, other_user])
        db.flush()

        conversation = Conversation(
            user_id=requester.id,
            session_id="terraform-plan-test-session",
            title="Terraform plan test",
        )
        db.add(conversation)
        db.flush()

        infra_request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements={
                "cloud_provider": "aws",
                "resource_type": "EC2",
                "environment": "development",
                "region": "ap-south-1",
                "vcpus": 2,
                "memory_gb": 4,
                "public_access": False,
                "operating_system": "amazon linux 2023",
            },
        )
        db.add(infra_request)
        db.commit()

        return {
            "request_id": infra_request.id,
            "requester_id": requester.id,
            "requester_headers": {
                "Authorization": (
                    f"Bearer {create_access_token(str(requester.id))}"
                )
            },
            "other_user_headers": {
                "Authorization": (
                    f"Bearer {create_access_token(str(other_user.id))}"
                )
            },
        }
    finally:
        db.close()


@pytest.fixture
def fake_result():
    return {
        "status": "succeeded",
        "preview_type": "trusted_server_terraform_plan",
        "is_real_terraform_plan": True,
        "source_verified": True,
        "deployment_enabled": False,
        "specification_fingerprint": "a" * 64,
        "plan_fingerprint": "b" * 64,
        "plan_summary": {
            "resource_summary": {
                "create": 1,
                "update": 0,
                "delete": 0,
            },
            "resources": [
                {
                    "address": "aws_instance.this",
                    "mode": "managed",
                    "type": "aws_instance",
                    "name": "this",
                    "actions": ["create"],
                }
            ],
            "terraform_version": "1.15.6",
            "format_version": "1.2",
        },
    }


def test_owner_can_create_trusted_plan_record(
    client, setup_data, fake_result, monkeypatch
):
    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        lambda specification: fake_result,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["deployment_enabled"] is False
    assert body["plan_fingerprint"] == "b" * 64

    db = TestingSessionLocal()
    try:
        record = db.get(TerraformPlanRecord, body["id"])
        assert record is not None
        assert record.status == "succeeded"
        assert record.plan_summary == fake_result["plan_summary"]
    finally:
        db.close()


def test_another_user_cannot_generate_plan(
    client, setup_data, monkeypatch
):
    def must_not_run(specification):
        pytest.fail("Terraform execution must not run for another user")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        must_not_run,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["other_user_headers"],
    )

    assert response.status_code == 404


def test_incomplete_request_cannot_generate_plan(
    client, setup_data, monkeypatch
):
    db = TestingSessionLocal()
    try:
        request = db.get(
            InfrastructureRequest,
            setup_data["request_id"],
        )
        request.status = "needs_input"
        db.commit()
    finally:
        db.close()

    def must_not_run(specification):
        pytest.fail("Terraform must not run for incomplete requirements")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        must_not_run,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409


def test_terraform_failure_is_recorded_safely(
    client, setup_data, monkeypatch
):
    def fail_execution(specification):
        raise TerraformExecutionError("COMMAND_FAILED")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        fail_execution,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "COMMAND_FAILED"

    db = TestingSessionLocal()
    try:
        record = db.get(
            TerraformPlanRecord,
            detail["plan_record_id"],
        )
        assert record is not None
        assert record.status == "failed"
        assert record.error_code == "COMMAND_FAILED"
        assert record.plan_summary == {}
    finally:
        db.close()
