import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlite3
from backend.app.main import app
from backend.app.core.dependencies import get_db
from backend.app.core.security import create_access_token
from backend.app.models.base import Base
from backend.app.models.approval import (
    ApprovalAuditEvent,
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_operation import InfrastructureOperation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.models.terraform_state_version import TerraformStateVersion
from backend.app.models.user import User

import backend.app.api.infrastructure_operations as operations_api


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
            email="operation-requester@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )

        other_user = User(
            email="operation-other@example.com",
            password_hash="test-hash",
            role="admin",
            is_active=True,
        )

        db.add_all([requester, other_user])
        db.flush()

        conversation = Conversation(
            user_id=requester.id,
            session_id="infrastructure-operation-test",
            title="Infrastructure operation test",
        )

        db.add(conversation)
        db.flush()

        infra_request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements={},
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

def test_owner_can_sync_state_versions(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        state_record = TerraformStateRecord(
            infrastructure_request_id=request_id,
            backend_type="s3",
            state_bucket="test-state-bucket",
            state_key=f"requests/{request_id}/terraform.tfstate",
            state_region="ap-south-1",
            status="active",
        )
        db.add(state_record)
        db.commit()
    finally:
        db.close()

    calls = []

    client_regions = []

    def fake_create_s3_client(region_name=None):
        client_regions.append(region_name)
        return "mock-s3-client"

    def fake_sync(db, client, request_id, bucket, key):
        calls.append((client, request_id, bucket, key))
        return {
            "total_versions": 2,
            "recorded": 2,
            "skipped": 0,
        }

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        fake_create_s3_client,
    )
    monkeypatch.setattr(
        operations_api,
        "sync_s3_state_versions",
        fake_sync,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 200, response.text

    body = response.json()

    assert body["infrastructure_request_id"] == request_id
    assert body["total_versions"] == 2
    assert body["recorded"] == 2
    assert body["skipped"] == 0
    assert body["versions"] == []

    assert calls == [
        (
            "mock-s3-client",
            request_id,
            "test-state-bucket",
            f"requests/{request_id}/terraform.tfstate",
        )
    ]

    assert client_regions == ["ap-south-1"]

def test_another_user_cannot_sync_state_versions(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=request_id,
                backend_type="s3",
                state_bucket="test-state-bucket",
                state_key=f"requests/{request_id}/terraform.tfstate",
                state_region="ap-south-1",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    def must_not_run():
        pytest.fail("S3 client must not be created for another user")

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["other_user_headers"],
    )

    assert response.status_code == 404

def test_unauthenticated_user_cannot_sync_state_versions(
    client, setup_data
):
    request_id = setup_data["request_id"]

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync"
    )

    assert response.status_code in (401, 403)

def test_sync_returns_404_when_state_record_does_not_exist(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    def must_not_run():
        pytest.fail("S3 client must not be created without a state record")

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Terraform state record not found."

def test_sync_rejects_unsupported_backend(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=request_id,
                backend_type="local",
                state_bucket="test-state-bucket",
                state_key=f"requests/{request_id}/terraform.tfstate",
                state_region="ap-south-1",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    def must_not_run():
        pytest.fail("S3 client must not be created for a non-S3 backend")

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Only S3 state backends are supported."

def test_sync_returns_502_when_s3_fails(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=request_id,
                backend_type="s3",
                state_bucket="test-state-bucket",
                state_key=f"requests/{request_id}/terraform.tfstate",
                state_region="ap-south-1",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    def fake_create_s3_client(region_name=None):
        assert region_name == "ap-south-1"
        return "mock-s3-client"

    def fake_sync(*args, **kwargs):
        raise operations_api.S3StateStorageError(
            "S3_STATE_VERSION_LIST_FAILED"
        )

    monkeypatch.setattr(
        operations_api, "create_s3_client", fake_create_s3_client
    )
    monkeypatch.setattr(
        operations_api, "sync_s3_state_versions", fake_sync
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Terraform state synchronization failed."
    )

def test_sync_returns_404_for_nonexistent_request(
    client, setup_data, monkeypatch
):
    def must_not_run():
        pytest.fail(
            "S3 client must not be created for a nonexistent request"
        )

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        "/infrastructure-operations/requests/999999/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 404

def test_sync_returns_409_when_state_version_retrieval_fails(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=request_id,
                backend_type="s3",
                state_bucket="test-state-bucket",
                state_key=f"requests/{request_id}/terraform.tfstate",
                state_region="ap-south-1",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    def fake_create_s3_client(region_name=None):
        assert region_name == "ap-south-1"
        return "mock-s3-client"


    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        fake_create_s3_client,
    )

    monkeypatch.setattr(
        operations_api,
        "sync_s3_state_versions",
        lambda *args, **kwargs: {
            "total_versions": 1,
            "recorded": 1,
            "skipped": 0,
        },
    )

    def fake_get_state_versions(*args, **kwargs):
        raise operations_api.TerraformStateVersionError(
            "STATE_VERSION_NOT_FOUND"
        )

    monkeypatch.setattr(
        operations_api,
        "get_state_versions",
        fake_get_state_versions,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Terraform state version retrieval failed."
    )

def test_sync_rejects_archived_state(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()
    try:
        db.add(
            TerraformStateRecord(
                infrastructure_request_id=request_id,
                backend_type="s3",
                state_bucket="test-state-bucket",
                state_key=f"requests/{request_id}/terraform.tfstate",
                state_region="ap-south-1",
                status="archived",
            )
        )
        db.commit()
    finally:
        db.close()

    def must_not_run():
        pytest.fail(
            "S3 client must not be created for archived state"
        )

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/state-versions/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Archived Terraform state cannot be synchronized."
    )


def test_provision_returns_409_for_database_uniqueness_conflict(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    db = TestingSessionLocal()

    try:
        infrastructure_request = db.get(
            InfrastructureRequest,
            request_id,
        )

        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=setup_data["requester_id"],
            status="succeeded",
            plan_fingerprint="a" * 64,
            specification_fingerprint="b" * 64,
            plan_summary={},
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request_id,
            terraform_plan_record_id=plan.id,
            requester_id=setup_data["requester_id"],
            status="approved",
            plan_summary={},
            plan_fingerprint="a" * 64,
        )
        db.add(approval)
        db.commit()

        approval_id = approval.id
        plan_id = plan.id

    finally:
        db.close()

    # Bypass plan validation because this test focuses specifically
    # on the database uniqueness-conflict handling.
    def fake_validate_approved_plan(
        approval,
        current_user_id,
        db,
    ):
        return (
            db.get(InfrastructureRequest, request_id),
            db.get(TerraformPlanRecord, plan_id),
        )

    monkeypatch.setattr(
        operations_api,
        "validate_approved_plan",
        fake_validate_approved_plan,
    )

    original_flush = Session.flush

    def simulate_unique_constraint_conflict(
        self,
        *args,
        **kwargs,
    ):
        if any(
            isinstance(item, InfrastructureOperation)
            for item in self.new
        ):
            raise operations_api.IntegrityError(
                "INSERT",
                {},
                sqlite3.IntegrityError(
                    "UNIQUE constraint failed: "
                    "infrastructure_operations.approval_request_id, "
                    "infrastructure_operations.operation"
                ),
            )

        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(
        Session,
        "flush",
        simulate_unique_constraint_conflict,
    )

    response = client.post(
        "/infrastructure-operations/provision",
        headers=setup_data["requester_headers"],
        json={
            "approval_request_id": approval_id,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A provision operation already exists for this approval."
    )
