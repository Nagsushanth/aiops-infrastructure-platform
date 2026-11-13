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
from datetime import datetime, timedelta, timezone

from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)
from backend.app.models.infrastructure_resource import InfrastructureResource

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

def test_provision_rejects_deprovision_plan(
    client,
    setup_data,
    monkeypatch,
):
    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    requirements = {
        "cloud_provider": "aws",
        "resource_type": "ec2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
        "operating_system": "Amazon Linux 2023",
    }

    specification = (
        operations_api.ValidatedInfrastructureSpecification
        .model_validate(requirements)
    )

    plan_checksum = "a" * 64

    db = TestingSessionLocal()

    try:
        infrastructure_request = db.get(
            InfrastructureRequest,
            request_id,
        )
        infrastructure_request.requirements = requirements

        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
            operation="deprovision",
            status="succeeded",
            plan_fingerprint=plan_checksum,
            specification_fingerprint=(
                operations_api.fingerprint_specification(specification)
            ),
            artifact_path="test-destroy-plan.tfplan",
            artifact_sha256=plan_checksum,
            plan_summary={},
        )

        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request_id,
            terraform_plan_record_id=plan.id,
            requester_id=requester_id,
            status="approved",
            plan_summary={},
            plan_fingerprint=plan_checksum,
        )

        db.add(approval)
        db.flush()

        for stage in ("technical", "cost", "security"):
            db.add(
                ApprovalStageReview(
                    approval_request_id=approval.id,
                    stage=stage,
                    status="approved",
                )
            )

        approval_id = approval.id
        db.commit()

    finally:
        db.close()

    # The test focuses on lifecycle validation, not filesystem access.
    monkeypatch.setattr(
        operations_api,
        "load_plan_artifact",
        lambda *args, **kwargs: b"test-plan",
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
        "Only provisioning plans can be provisioned."
    )

    # Confirm that no infrastructure operation was created.
    db = TestingSessionLocal()
    try:
        operations = db.query(InfrastructureOperation).all()
        assert operations == []
    finally:
        db.close()

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

def test_operation_audit_is_visible_to_owner_only_and_chronological(
    client,
    setup_data,
):
    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    db = TestingSessionLocal()

    try:
        # Create a Terraform plan.
        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
            status="succeeded",
            plan_fingerprint="a" * 64,
            specification_fingerprint="b" * 64,
            plan_summary={},
        )
        db.add(plan)
        db.flush()

        # Create an approved approval request.
        approval = ApprovalRequest(
            infrastructure_request_id=request_id,
            terraform_plan_record_id=plan.id,
            requester_id=requester_id,
            status="approved",
            plan_summary={},
            plan_fingerprint="a" * 64,
        )
        db.add(approval)
        db.flush()

        # Create the infrastructure operation.
        operation = InfrastructureOperation(
            infrastructure_request_id=request_id,
            terraform_plan_record_id=plan.id,
            approval_request_id=approval.id,
            requester_id=requester_id,
            operation="provision",
            status="failed",
            error_code="WORKER_STALE_OPERATION",
            result_summary={"reason": "Worker lease expired"},
        )
        db.add(operation)
        db.flush()

        now = datetime.now(timezone.utc)

        # Existing approval audit event.
        approval_event = ApprovalAuditEvent(
            approval_request_id=approval.id,
            actor_id=requester_id,
            event_type="provision_requested",
            previous_status=None,
            new_status="pending",
            comments="Provisioning requested",
            event_metadata={"source": "test"},
            created_at=now - timedelta(minutes=2),
        )

        # System-generated recovery event.
        recovery_event = InfrastructureOperationEvent(
            operation_id=operation.id,
            actor_id=None,
            actor_type="system",
            event_type="worker_lease_expired",
            previous_status="running",
            new_status="failed",
            comments="Worker lease expired; manual review required.",
            event_metadata={
                "error_code": "WORKER_STALE_OPERATION",
                "automatic_retry": False,
            },
            created_at=now,
        )

        db.add_all([approval_event, recovery_event])
        db.commit()

        operation_id = operation.id

    finally:
        db.close()

    # Owner must be able to retrieve both audit events.
    response = client.get(
        f"/infrastructure-operations/{operation_id}/audit",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 200, response.text

    events = response.json()

    assert len(events) == 2

    # Both approval and infrastructure operation events must be visible.
    assert {event["source"] for event in events} == {
        "approval",
        "infrastructure_operation",
    }

    event_types = {event["event_type"] for event in events}

    assert "provision_requested" in event_types
    assert "worker_lease_expired" in event_types

    # Verify the system recovery event has no human actor.
    recovery_events = [
        event
        for event in events
        if event["event_type"] == "worker_lease_expired"
    ]

    assert len(recovery_events) == 1
    assert recovery_events[0]["actor_type"] == "system"
    assert recovery_events[0]["actor_id"] is None
    assert recovery_events[0]["new_status"] == "failed"

    # Verify the approval event remains attributed to the requester.
    approval_events = [
        event
        for event in events
        if event["event_type"] == "provision_requested"
    ]

    assert len(approval_events) == 1
    assert approval_events[0]["actor_type"] == "user"
    assert approval_events[0]["actor_id"] == requester_id

    # Verify events are returned oldest to newest.
    timestamps = [
        datetime.fromisoformat(event["created_at"])
        for event in events
    ]

    assert timestamps == sorted(timestamps)

    assert events[0]["event_type"] == "provision_requested"
    assert events[1]["event_type"] == "worker_lease_expired"

    # Another user must not access this operation's audit history.
    other_user_response = client.get(
        f"/infrastructure-operations/{operation_id}/audit",
        headers=setup_data["other_user_headers"],
    )

    assert other_user_response.status_code == 404
    assert other_user_response.json()["detail"] == (
        "Infrastructure operation not found."
    )

def test_provision_dispatch_failure_is_audited(
    client,
    setup_data,
    monkeypatch,
):
    from backend.app.workers import tasks

    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    db = TestingSessionLocal()

    try:
        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
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
            requester_id=requester_id,
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

    def fake_validate_approved_plan(
        approval,
        current_user_id,
        db,
    ):
        return (
            db.get(InfrastructureRequest, request_id),
            db.get(TerraformPlanRecord, plan_id),
        )

    def fail_dispatch(operation_id):
        raise RuntimeError("Broker unavailable")

    monkeypatch.setattr(
        operations_api,
        "validate_approved_plan",
        fake_validate_approved_plan,
    )

    monkeypatch.setattr(
        tasks.execute_provision_operation,
        "delay",
        fail_dispatch,
    )

    response = client.post(
        "/infrastructure-operations/provision",
        headers=setup_data["requester_headers"],
        json={"approval_request_id": approval_id},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "CELERY_DISPATCH_FAILED"

    operation_id = response.json()["id"]

    db = TestingSessionLocal()

    try:
        event = db.query(InfrastructureOperationEvent).filter_by(
            operation_id=operation_id,
            event_type="celery_dispatch_failed",
        ).one()

        assert event.actor_type == "system"
        assert event.actor_id is None
        assert event.previous_status == "queued"
        assert event.new_status == "failed"
        assert event.event_metadata["automatic_retry"] is False

    finally:
        db.close()

def test_owner_can_sync_infrastructure_inventory(
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

    client_regions = []
    read_calls = []

    def fake_create_s3_client(region_name=None):
        client_regions.append(region_name)
        return "mock-s3-client"

    def fake_read_state_object(client, bucket, key):
        read_calls.append((client, bucket, key))
        return {
            "content": b"mock-terraform-state",
            "version_id": "test-version-1",
            "sha256": "a" * 64,
        }

    resources = [
        {
            "resource_address": "aws_instance.web",
            "resource_type": "aws_instance",
            "resource_name": "web",
            "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
            "mode": "managed",
            "module": None,
            "instance_key": None,
            "resource_id": "i-test123",
            "tags": {"Name": "web"},
            "dependencies": [],
            "instance_status": "unknown",
        }
    ]

    def fake_parse_terraform_state(content):
        assert content == b"mock-terraform-state"
        return {
            "serial": 1,
            "lineage": "test-lineage",
            "resources": resources,
        }

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        fake_create_s3_client,
    )
    monkeypatch.setattr(
        operations_api,
        "read_state_object",
        fake_read_state_object,
    )
    monkeypatch.setattr(
        operations_api,
        "parse_terraform_state",
        fake_parse_terraform_state,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/inventory/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 200, response.text

    body = response.json()

    assert body == {
        "infrastructure_request_id": request_id,
        "total_resources": 1,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "marked_absent": 0,
    }

    assert client_regions == ["ap-south-1"]

    assert read_calls == [
        (
            "mock-s3-client",
            "test-state-bucket",
            f"requests/{request_id}/terraform.tfstate",
        )
    ]

    db = TestingSessionLocal()
    try:
        inventory = db.query(InfrastructureResource).filter_by(
            infrastructure_request_id=request_id
        ).one()

        assert inventory.resource_address == "aws_instance.web"
        assert inventory.resource_type == "aws_instance"
        assert inventory.resource_id == "i-test123"
        assert inventory.state_serial == 1
        assert inventory.is_present is True
        state_record = db.query(TerraformStateRecord).filter_by(
            infrastructure_request_id=request_id
        ).one()

        assert state_record.state_version_id == "test-version-1"
        assert state_record.state_serial == 1
        assert state_record.state_lineage == "test-lineage"
        assert state_record.state_sha256 == "a" * 64
    finally:
        db.close()

def test_another_user_cannot_sync_infrastructure_inventory(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    def must_not_run(*args, **kwargs):
        pytest.fail("S3 must not be accessed by another user")

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        must_not_run,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/inventory/sync",
        headers=setup_data["other_user_headers"],
    )

    assert response.status_code == 404

def test_inventory_sync_returns_502_for_invalid_state(
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

    monkeypatch.setattr(
        operations_api,
        "create_s3_client",
        lambda region_name=None: "mock-s3-client",
    )

    monkeypatch.setattr(
        operations_api,
        "read_state_object",
        lambda *args, **kwargs: {
            "content": b"invalid-state",
        },
    )

    def fake_parse(content):
        raise operations_api.S3StateStorageError(
            "S3_STATE_JSON_INVALID"
        )

    monkeypatch.setattr(
        operations_api,
        "parse_terraform_state",
        fake_parse,
    )

    response = client.post(
        f"/infrastructure-operations/requests/{request_id}/inventory/sync",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "Terraform state retrieval or validation failed."
    )

def test_inventory_sync_updates_unchanged_and_marks_absent(
    client, setup_data
):
    request_id = setup_data["request_id"]

    resource = {
        "resource_address": "aws_instance.web",
        "resource_type": "aws_instance",
        "resource_name": "web",
        "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
        "mode": "managed",
        "module": None,
        "instance_key": None,
        "resource_id": "i-test123",
        "tags": {"Name": "web"},
        "dependencies": [],
        "instance_status": "unknown",
    }

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

        # First synchronization: create resource.
        result = operations_api.sync_infrastructure_inventory(
            db, request_id, 1, [resource]
        )

        assert result["created"] == 1

        # Second synchronization: unchanged resource.
        result = operations_api.sync_infrastructure_inventory(
            db, request_id, 1, [resource]
        )

        assert result["unchanged"] == 1
        assert result["created"] == 0

        # Third synchronization: update resource metadata.
        updated_resource = {
            **resource,
            "tags": {"Name": "web-updated"},
        }

        result = operations_api.sync_infrastructure_inventory(
            db, request_id, 2, [updated_resource]
        )

        assert result["updated"] == 1

        # Fourth synchronization: resource is absent from newer state.
        result = operations_api.sync_infrastructure_inventory(
            db, request_id, 3, []
        )

        assert result["total_resources"] == 0
        assert result["marked_absent"] == 1

        inventory = db.query(InfrastructureResource).filter_by(
            infrastructure_request_id=request_id
        ).one()

        assert inventory.is_present is False
        assert inventory.state_serial == 3

    finally:
        db.close()

def test_empty_inventory_state_serial_is_persisted_and_protected(
    client, setup_data
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

        # Synchronize an empty state with serial 5.
        result = operations_api.sync_infrastructure_inventory(
            db,
            request_id,
            5,
            [],
            state_version_id="empty-state-version",
            state_lineage="test-empty-lineage",
            state_sha256="b" * 64,
        )

        assert result["total_resources"] == 0
        assert result["created"] == 0
        assert result["marked_absent"] == 0

        db.refresh(state_record)
        assert state_record.state_serial == 5
        assert state_record.state_version_id == "empty-state-version"
        assert state_record.state_lineage == "test-empty-lineage"
        assert state_record.state_sha256 == "b" * 64

        # An older state must not overwrite the latest state metadata.
        with pytest.raises(
            operations_api.InfrastructureInventoryError,
            match="INVENTORY_STATE_SERIAL_STALE",
        ):
            operations_api.sync_infrastructure_inventory(
                db,
                request_id,
                4,
                [],
                state_version_id="older-version",
                state_lineage="older-lineage",
                state_sha256="c" * 64,
            )

        db.refresh(state_record)
        assert state_record.state_serial == 5
        assert state_record.state_version_id == "empty-state-version"
        assert state_record.state_lineage == "test-empty-lineage"
        assert state_record.state_sha256 == "b" * 64

    finally:
        db.close()

@pytest.mark.parametrize(
    "payload",
    [
        {"approval_request_id": 1},
        {
            "approval_request_id": 1,
            "confirm_destroy": False,
        },
    ],
)
def test_deprovision_requires_explicit_destroy_confirmation(
    client,
    setup_data,
    payload,
):
    response = client.post(
        "/infrastructure-operations/deprovision",
        headers=setup_data["requester_headers"],
        json=payload,
    )

    assert response.status_code == 422

def test_deprovision_creates_confirmed_operation(
    client,
    setup_data,
    monkeypatch,
):
    from types import SimpleNamespace
    from backend.app.workers import tasks

    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    db = TestingSessionLocal()

    try:
        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
            operation="deprovision",
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
            requester_id=requester_id,
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

    def fake_validate_approved_plan(
        approval,
        current_user_id,
        db,
        expected_operation="provision",
    ):
        assert expected_operation == "deprovision"

        return (
            db.get(InfrastructureRequest, request_id),
            db.get(TerraformPlanRecord, plan_id),
        )

    dispatched = []

    def fake_dispatch(operation_id):
        dispatched.append(operation_id)
        return SimpleNamespace(id="test-deprovision-task")

    monkeypatch.setattr(
        operations_api,
        "validate_approved_plan",
        fake_validate_approved_plan,
    )

    monkeypatch.setattr(
        tasks.execute_deprovision_operation,
        "delay",
        fake_dispatch,
    )

    response = client.post(
        "/infrastructure-operations/deprovision",
        headers=setup_data["requester_headers"],
        json={
            "approval_request_id": approval_id,
            "confirm_destroy": True,
            "comments": "Confirmed deprovision request",
        },
    )

    assert response.status_code == 202, response.text

    body = response.json()

    assert body["operation"] == "deprovision"
    assert body["status"] == "queued"
    assert body["celery_task_id"] == "test-deprovision-task"
    assert body["result_summary"]["destroy_confirmed"] is True
    assert body["result_summary"]["terraform_destroy_enabled"] is False

    assert dispatched == [body["id"]]

    db = TestingSessionLocal()

    try:
        audit = db.query(ApprovalAuditEvent).filter_by(
            approval_request_id=approval_id,
            event_type="deprovision_requested",
        ).one()

        assert audit.actor_id == requester_id
        assert audit.event_metadata["destroy_confirmed"] is True

    finally:
        db.close()

def test_deprovision_dispatch_failure_is_audited(
    client,
    setup_data,
    monkeypatch,
):
    from backend.app.workers import tasks

    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    db = TestingSessionLocal()

    try:
        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
            operation="deprovision",
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
            requester_id=requester_id,
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

    def fake_validate_approved_plan(
        approval,
        current_user_id,
        db,
        expected_operation="provision",
    ):
        assert expected_operation == "deprovision"

        return (
            db.get(InfrastructureRequest, request_id),
            db.get(TerraformPlanRecord, plan_id),
        )

    def fail_dispatch(operation_id):
        raise RuntimeError("Broker unavailable")

    monkeypatch.setattr(
        operations_api,
        "validate_approved_plan",
        fake_validate_approved_plan,
    )

    monkeypatch.setattr(
        tasks.execute_deprovision_operation,
        "delay",
        fail_dispatch,
    )

    response = client.post(
        "/infrastructure-operations/deprovision",
        headers=setup_data["requester_headers"],
        json={
            "approval_request_id": approval_id,
            "confirm_destroy": True,
            "comments": "Confirmed deprovision request",
        },
    )

    assert response.status_code == 202, response.text

    body = response.json()

    assert body["status"] == "failed"
    assert body["error_code"] == "CELERY_DISPATCH_FAILED"
    assert body["result_summary"]["destroy_confirmed"] is True
    assert body["result_summary"]["terraform_destroy_enabled"] is False

    operation_id = body["id"]

    db = TestingSessionLocal()

    try:
        event = db.query(InfrastructureOperationEvent).filter_by(
            operation_id=operation_id,
            event_type="celery_dispatch_failed",
        ).one()

        assert event.actor_type == "system"
        assert event.actor_id is None
        assert event.previous_status == "queued"
        assert event.new_status == "failed"
        assert event.event_metadata["automatic_retry"] is False

    finally:
        db.close()

def test_deprovision_returns_409_for_database_uniqueness_conflict(
    client,
    setup_data,
    monkeypatch,
):
    request_id = setup_data["request_id"]
    requester_id = setup_data["requester_id"]

    db = TestingSessionLocal()

    try:
        plan = TerraformPlanRecord(
            infrastructure_request_id=request_id,
            requester_id=requester_id,
            operation="deprovision",
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
            requester_id=requester_id,
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

    def fake_validate_approved_plan(
        approval,
        current_user_id,
        db,
        expected_operation="provision",
    ):
        assert expected_operation == "deprovision"

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
        "/infrastructure-operations/deprovision",
        headers=setup_data["requester_headers"],
        json={
            "approval_request_id": approval_id,
            "confirm_destroy": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A deprovision operation already exists for this approval."
    )

def _create_health_test_records(request_id):
    """Create Terraform state and one inventoried EC2 instance."""

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

        db.add(
            InfrastructureResource(
                infrastructure_request_id=request_id,
                resource_address="aws_instance.web",
                resource_type="aws_instance",
                resource_name="web",
                provider="registry.terraform.io/hashicorp/aws",
                mode="managed",
                module=None,
                instance_key=None,
                resource_id="i-1234567890abcdef0",
                tags={},
                dependencies=[],
                instance_status="running",
                state_serial=1,
                is_present=True,
            )
        )

        db.commit()
    finally:
        db.close()


def test_owner_can_retrieve_ec2_resource_health(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]
    _create_health_test_records(request_id)

    class FakeEC2Client:
        def describe_instances(self, **kwargs):
            assert kwargs["InstanceIds"] == ["i-1234567890abcdef0"]
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1234567890abcdef0",
                                "State": {"Name": "running"},
                            }
                        ]
                    }
                ]
            }

        def describe_instance_status(self, **kwargs):
            assert kwargs["IncludeAllInstances"] is True
            return {
                "InstanceStatuses": [
                    {
                        "InstanceId": "i-1234567890abcdef0",
                        "SystemStatus": {"Status": "ok"},
                        "InstanceStatus": {"Status": "ok"},
                    }
                ]
            }

    regions = []

    def fake_create_ec2_client(region_name):
        regions.append(region_name)
        return FakeEC2Client()

    monkeypatch.setattr(
        operations_api,
        "create_ec2_client",
        fake_create_ec2_client,
    )

    response = client.get(
        f"/infrastructure-operations/requests/{request_id}/health",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 200, response.text

    body = response.json()

    assert body["infrastructure_request_id"] == request_id
    assert body["total_instances"] == 1
    assert body["health"][0]["instance_id"] == "i-1234567890abcdef0"
    assert body["health"][0]["lifecycle_state"] == "running"
    assert body["health"][0]["health_status"] == "healthy"
    assert regions == ["ap-south-1"]

    # Live monitoring must not overwrite Terraform inventory status.
    db = TestingSessionLocal()
    try:
        resource = db.query(InfrastructureResource).filter_by(
            infrastructure_request_id=request_id
        ).one()

        assert resource.instance_status == "running"
    finally:
        db.close()

def test_another_user_cannot_retrieve_ec2_resource_health(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    def must_not_run(*args, **kwargs):
        pytest.fail("EC2 client must not be created for another user")

    monkeypatch.setattr(
        operations_api,
        "create_ec2_client",
        must_not_run,
    )

    response = client.get(
        f"/infrastructure-operations/requests/{request_id}/health",
        headers=setup_data["other_user_headers"],
    )

    assert response.status_code == 404

def test_health_returns_empty_inventory_without_aws_call(
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

    def must_not_run(*args, **kwargs):
        pytest.fail("EC2 must not be queried for empty inventory")

    monkeypatch.setattr(
        operations_api,
        "create_ec2_client",
        must_not_run,
    )

    response = client.get(
        f"/infrastructure-operations/requests/{request_id}/health",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 200
    assert response.json() == {
        "infrastructure_request_id": request_id,
        "total_instances": 0,
        "health": [],
    }

def test_health_returns_404_when_state_record_is_missing(
    client, setup_data, monkeypatch
):
    request_id = setup_data["request_id"]

    def must_not_run(*args, **kwargs):
        pytest.fail("EC2 must not be queried without a state record")

    monkeypatch.setattr(
        operations_api,
        "create_ec2_client",
        must_not_run,
    )

    response = client.get(
        f"/infrastructure-operations/requests/{request_id}/health",
        headers=setup_data["requester_headers"],
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Terraform state record not found."