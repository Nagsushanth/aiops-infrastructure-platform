import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os
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
from backend.app.models.terraform_state import TerraformStateRecord


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
def client(monkeypatch):

    monkeypatch.setenv("TF_STATE_BUCKET", "test-terraform-state")
    monkeypatch.setenv("TF_STATE_REGION", "ap-south-1")
    monkeypatch.setenv("TF_STATE_PREFIX", "test/requests")
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
        "artifact_path": "/tmp/test-artifacts/mock.tfplan",
        "artifact_sha256": "c" * 64,
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
        lambda specification, **kwargs: fake_result,
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
        assert record.artifact_path == fake_result["artifact_path"]
        assert record.artifact_sha256 == fake_result["artifact_sha256"]
        state_record = db.query(TerraformStateRecord).filter_by(
            infrastructure_request_id=setup_data["request_id"]
        ).one()

        assert state_record.backend_type == "s3"
        assert state_record.state_bucket == "test-terraform-state"
        assert (
            state_record.state_key
            == f"test/requests/{setup_data['request_id']}/terraform.tfstate"
        )
        assert state_record.state_region == "ap-south-1"
        assert state_record.status == "initializing"
        assert state_record.state_version_id is None
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
    def fail_execution(specification, **kwargs):
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

def test_missing_request_does_not_execute_terraform(
    client, setup_data, monkeypatch
):
    def must_not_run(specification):
        pytest.fail("Terraform must not run for a missing request")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        must_not_run,
    )

    response = client.post(
        "/terraform/requests/999999/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 404


def test_unauthenticated_request_does_not_execute_terraform(
    client, setup_data, monkeypatch
):
    def must_not_run(specification):
        pytest.fail("Terraform must not run without authentication")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        must_not_run,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
    )

    assert response.status_code in (401, 403)


def test_successful_plan_keeps_deployment_disabled(
    client, setup_data, fake_result, monkeypatch
):
    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        lambda specification, **kwargs: fake_result,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 201, response.text

    body = response.json()
    assert body["deployment_enabled"] is False

    db = TestingSessionLocal()
    try:
        record = db.get(TerraformPlanRecord, body["id"])
        assert record is not None
        assert record.status == "succeeded"
        assert record.plan_fingerprint == "b" * 64
    finally:
        db.close()

def test_repeated_plan_request_reuses_state_record(
    client, setup_data, fake_result, monkeypatch
):
    execution_calls = []

    def fake_execution(specification, **kwargs):
        execution_calls.append(kwargs["infrastructure_request_id"])
        return fake_result

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        fake_execution,
    )

    endpoint = (
        f"/terraform/requests/{setup_data['request_id']}/plan"
    )
    headers = setup_data["requester_headers"]

    first_response = client.post(endpoint, headers=headers)
    second_response = client.post(endpoint, headers=headers)

    assert first_response.status_code == 201, first_response.text
    assert second_response.status_code == 201, second_response.text

    assert execution_calls == [
        setup_data["request_id"],
        setup_data["request_id"],
    ]

    db = TestingSessionLocal()
    try:
        state_records = (
            db.query(TerraformStateRecord)
            .filter_by(
                infrastructure_request_id=setup_data["request_id"]
            )
            .all()
        )

        assert len(state_records) == 1

        state_record = state_records[0]

        assert state_record.backend_type == "s3"
        assert state_record.state_bucket == "test-terraform-state"
        assert (
            state_record.state_key
            == f"test/requests/{setup_data['request_id']}/terraform.tfstate"
        )
        assert state_record.status == "initializing"
    finally:
        db.close()

def test_plan_rejects_mismatched_existing_state_backend(
    client, setup_data, monkeypatch
):
    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=setup_data["request_id"],
                backend_type="s3",
                state_bucket="different-state-bucket",
                state_key=(
                    f"test/requests/{setup_data['request_id']}/terraform.tfstate"
                ),
                state_region="ap-south-1",
                status="pending",
            )
        )
        db.commit()
    finally:
        db.close()

    def must_not_run(specification, **kwargs):
        pytest.fail("Terraform must not run with a mismatched state backend")

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
    assert "does not match" in response.json()["detail"]

def test_backend_initialization_failure_marks_new_state_failed(
    client, setup_data, monkeypatch
):
    def fail_execution(specification, **kwargs):
        raise TerraformExecutionError("INIT_BACKEND_FAILED")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        fail_execution,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "INIT_BACKEND_FAILED"

    db = TestingSessionLocal()
    try:
        state_record = db.query(TerraformStateRecord).filter_by(
            infrastructure_request_id=setup_data["request_id"]
        ).one()

        assert state_record.status == "failed"
    finally:
        db.close()

def test_backend_initialization_failure_preserves_active_state(
    client, setup_data, monkeypatch
):
    db = TestingSessionLocal()
    try:
        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket="test-terraform-state",
            state_key=(
                f"test/requests/{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region="ap-south-1",
            status="active",
        )
        db.add(state_record)
        db.commit()
    finally:
        db.close()

    def fail_execution(specification, **kwargs):
        raise TerraformExecutionError("INIT_BACKEND_FAILED")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_plan",
        fail_execution,
    )

    response = client.post(
        f"/terraform/requests/{setup_data['request_id']}/plan",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 422

    db = TestingSessionLocal()
    try:
        state_record = db.query(TerraformStateRecord).filter_by(
            infrastructure_request_id=setup_data["request_id"]
        ).one()

        assert state_record.status == "active"
    finally:
        db.close()

def test_destroy_plan_requires_successful_provisioning_plan(
    client,
    setup_data,
    monkeypatch,
):
    def must_not_run(**kwargs):
        pytest.fail(
            "Destroy planning must not run without a successful "
            "provisioning plan"
        )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        must_not_run,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "No successful provisioning plan is available."
    )

def test_another_user_cannot_generate_destroy_plan(
    client,
    setup_data,
    monkeypatch,
):
    def must_not_run(**kwargs):
        pytest.fail(
            "Destroy planning must not run for another user"
        )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        must_not_run,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["other_user_headers"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "Infrastructure request not found."
    )

def test_destroy_plan_requires_state_record(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()

    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        db.add(source_plan)
        db.commit()
    finally:
        db.close()

    def must_not_run(**kwargs):
        pytest.fail("Terraform must not run without a state record")

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        must_not_run,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state record not found."
    )

def test_destroy_plan_rejects_inactive_state(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()

    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="initializing",
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    def must_not_run(**kwargs):
        pytest.fail(
            "Terraform destroy planning must not run "
            "with inactive state"
        )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        must_not_run,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state is not active."
    )

def test_destroy_plan_rejects_incomplete_state_snapshot(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()

    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    def must_not_run(*args, **kwargs):
        pytest.fail(
            "AWS or Terraform must not run with an incomplete state snapshot"
        )

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        must_not_run,
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        must_not_run,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state snapshot is incomplete."
    )

def test_destroy_plan_rejects_stale_state_snapshot(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()

    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="old-version",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        lambda *args, **kwargs: {
            "content": b'{"version":4,"serial":6,"lineage":"12345678-1234-4234-8234-123456789abc","resources":[]}',
            "version_id": "new-version",
            "sha256": "b" * 64,
        },
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        lambda **kwargs: pytest.fail(
            "Terraform must not run when the state snapshot is stale"
        ),
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state has changed. Synchronize inventory before continuing."
    )

def test_destroy_plan_handles_s3_state_read_failure(
    client,
    setup_data,
    monkeypatch,
):
    from backend.app.services.terraform.s3_state_storage import (
        S3StateStorageError,
    )

    db = TestingSessionLocal()

    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="version-1",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    def fail_state_read(*args, **kwargs):
        raise S3StateStorageError("S3_STATE_READ_FAILED")

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        fail_state_read,
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        lambda **kwargs: pytest.fail(
            "Terraform must not run when S3 state reading fails"
        ),
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Terraform state could not be verified."
    )

def test_destroy_plan_handles_terraform_execution_failure(
    client,
    setup_data,
    monkeypatch,
):
    from backend.app.services.terraform.execution import (
        TerraformExecutionError,
    )

    db = TestingSessionLocal()
    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="version-1",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        lambda *args, **kwargs: {
            "content": (
                b'{"version":4,"serial":5,'
                b'"lineage":"12345678-1234-4234-8234-123456789abc",'
                b'"resources":[]}'
            ),
            "version_id": "version-1",
            "sha256": "a" * 64,
        },
    )

    def fail_destroy_plan(**kwargs):
        raise TerraformExecutionError(
            "DESTROY_PLAN_FAILED"
        )

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        fail_destroy_plan,
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "message": "Terraform destroy-plan generation failed.",
        "error_code": "DESTROY_PLAN_FAILED",
    }

    db = TestingSessionLocal()
    try:
        destroy_plans = db.scalars(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.infrastructure_request_id
                == setup_data["request_id"],
                TerraformPlanRecord.operation == "deprovision",
            )
        ).all()

        assert destroy_plans == []
    finally:
        db.close()


def test_destroy_plan_rejects_state_change_during_generation(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()
    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="version-1",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    state_reads = {"count": 0}

    def read_changing_state(*args, **kwargs):
        state_reads["count"] += 1

        if state_reads["count"] == 1:
            return {
                "content": (
                    b'{"version":4,"serial":5,'
                    b'"lineage":"12345678-1234-4234-8234-123456789abc",'
                    b'"resources":[]}'
                ),
                "version_id": "version-1",
                "sha256": "a" * 64,
            }

        return {
            "content": (
                b'{"version":4,"serial":6,'
                b'"lineage":"12345678-1234-4234-8234-123456789abc",'
                b'"resources":[]}'
            ),
            "version_id": "version-2",
            "sha256": "f" * 64,
        }

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        read_changing_state,
    )

    artifact_path = "/tmp/generated-destroy.tfplan"

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        lambda **kwargs: {
            "plan_fingerprint": "f" * 64,
            "plan_summary": {},
            "artifact_path": artifact_path,
            "artifact_sha256": "e" * 64,
        },
    )

    deleted_artifacts = []

    monkeypatch.setattr(
        terraform_plans_api,
        "delete_plan_artifact",
        lambda path: deleted_artifacts.append(path),
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state has changed. Synchronize inventory before continuing."
    )

    assert state_reads["count"] == 2
    assert deleted_artifacts == [artifact_path]

    db = TestingSessionLocal()
    try:
        destroy_plans = db.scalars(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.infrastructure_request_id
                == setup_data["request_id"],
                TerraformPlanRecord.operation == "deprovision",
            )
        ).all()

        assert destroy_plans == []
    finally:
        db.close()


def test_destroy_plan_successfully_persists_deprovision_record(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()
    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="version-1",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        lambda *args, **kwargs: {
            "content": (
                b'{"version":4,"serial":5,'
                b'"lineage":"12345678-1234-4234-8234-123456789abc",'
                b'"resources":[]}'
            ),
            "version_id": "version-1",
            "sha256": "a" * 64,
        },
    )

    generated_artifact = "/tmp/generated-destroy.tfplan"

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        lambda **kwargs: {
            "status": "succeeded",
            "operation": "deprovision",
            "plan_fingerprint": "f" * 64,
            "plan_summary": {
                "resources": [],
                "deployment_enabled": False,
            },
            "artifact_path": generated_artifact,
            "artifact_sha256": "9" * 64,
        },
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 201

    response_data = response.json()

    assert response_data["operation"] == "deprovision"
    assert response_data["status"] == "succeeded"
    assert response_data["deployment_enabled"] is False
    assert response_data["plan_fingerprint"] == "f" * 64

    db = TestingSessionLocal()
    try:
        destroy_plan = db.scalar(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.infrastructure_request_id
                == setup_data["request_id"],
                TerraformPlanRecord.operation == "deprovision",
            )
        )

        assert destroy_plan is not None
        assert destroy_plan.status == "succeeded"
        assert destroy_plan.artifact_path == generated_artifact
        assert destroy_plan.artifact_sha256 == "9" * 64
        assert destroy_plan.state_version_id == "version-1"
        assert destroy_plan.state_serial == 5
        assert destroy_plan.state_lineage == (
            "12345678-1234-4234-8234-123456789abc"
        )
        assert destroy_plan.state_sha256 == "a" * 64
        assert destroy_plan.configuration_artifact_path == (
            "/tmp/source-config.tar"
        )
        assert destroy_plan.provider_lock_path == (
            "/tmp/source-lock.hcl"
        )
    finally:
        db.close()


def test_destroy_plan_commit_failure_deletes_artifact(
    client,
    setup_data,
    monkeypatch,
):
    db = TestingSessionLocal()
    try:
        source_plan = TerraformPlanRecord(
            infrastructure_request_id=setup_data["request_id"],
            requester_id=setup_data["requester_id"],
            operation="provision",
            status="succeeded",
            plan_fingerprint="b" * 64,
            specification_fingerprint="a" * 64,
            artifact_path="/tmp/source.tfplan",
            artifact_sha256="c" * 64,
            configuration_artifact_path="/tmp/source-config.tar",
            configuration_artifact_sha256="d" * 64,
            provider_lock_path="/tmp/source-lock.hcl",
            provider_lock_sha256="e" * 64,
            plan_summary={},
        )

        state_record = TerraformStateRecord(
            infrastructure_request_id=setup_data["request_id"],
            backend_type="s3",
            state_bucket=os.getenv("TF_STATE_BUCKET"),
            state_key=(
                f"{os.getenv('TF_STATE_PREFIX').rstrip('/')}/"
                f"{setup_data['request_id']}/terraform.tfstate"
            ),
            state_region=os.getenv("TF_STATE_REGION"),
            status="active",
            state_version_id="version-1",
            state_serial=5,
            state_lineage="12345678-1234-4234-8234-123456789abc",
            state_sha256="a" * 64,
        )

        db.add_all([source_plan, state_record])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        terraform_plans_api,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        terraform_plans_api,
        "read_state_object",
        lambda *args, **kwargs: {
            "content": (
                b'{"version":4,"serial":5,'
                b'"lineage":"12345678-1234-4234-8234-123456789abc",'
                b'"resources":[]}'
            ),
            "version_id": "version-1",
            "sha256": "a" * 64,
        },
    )

    artifact_path = "/tmp/generated-destroy.tfplan"

    monkeypatch.setattr(
        terraform_plans_api,
        "execute_terraform_destroy_plan",
        lambda **kwargs: {
            "plan_fingerprint": "f" * 64,
            "plan_summary": {},
            "artifact_path": artifact_path,
            "artifact_sha256": "9" * 64,
        },
    )

    deleted_artifacts = []

    monkeypatch.setattr(
        terraform_plans_api,
        "delete_plan_artifact",
        lambda path: deleted_artifacts.append(path),
    )

    def override_get_db_with_commit_failure():
        session = TestingSessionLocal()

        class CommitFailingSession:
            def __init__(self, session):
                self.session = session

            def __getattr__(self, name):
                return getattr(self.session, name)

            def commit(self):
                raise RuntimeError(
                    "Simulated database commit failure"
                )

            def rollback(self):
                self.session.rollback()

            def close(self):
                self.session.close()

        failing_session = CommitFailingSession(session)

        try:
            yield failing_session
        finally:
            failing_session.close()


    app.dependency_overrides[get_db] = (
        override_get_db_with_commit_failure
    )

    response = client.post(
        (
            f"/terraform/requests/"
            f"{setup_data['request_id']}/destroy-plan"
        ),
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Terraform destroy-plan record could not be saved."
    )

    assert deleted_artifacts == [artifact_path]

    db = TestingSessionLocal()
    try:
        destroy_plans = db.scalars(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.infrastructure_request_id
                == setup_data["request_id"],
                TerraformPlanRecord.operation == "deprovision",
            )
        ).all()

        assert destroy_plans == []
    finally:
        db.close()