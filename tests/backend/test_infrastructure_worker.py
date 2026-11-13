from types import SimpleNamespace
import pytest
import hashlib
import json
from sqlalchemy import select
import os
import sys
import time
from pathlib import Path
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
)
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    save_plan_artifact,
    delete_plan_artifact,
    save_provider_lock_artifact,
    delete_provider_lock_artifact,
    save_configuration_artifact,
    delete_configuration_artifact,
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager
from backend.app.models.base import Base
from backend.app.models.approval import (
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_operation import (
    InfrastructureOperation,
)
from backend.app.models.infrastructure_request import (
    InfrastructureRequest,
)
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.models.user import User

from backend.app.workers import tasks
from datetime import datetime, timedelta, timezone

from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)
from backend.app.models.infrastructure_resource import (
    InfrastructureResource,
)


@pytest.fixture
def worker_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        TestingSessionLocal,
    )

    yield TestingSessionLocal

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_worker_returns_not_found_for_missing_operation(worker_db):
    result = tasks.execute_provision_operation.run(9999)

    assert result["status"] == "not_found"
    assert result["operation_id"] == 9999


def test_worker_ignores_operation_that_is_not_queued(worker_db):
    db = worker_db()

    try:
        user = User(
            email="worker@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()

        conversation = Conversation(
            user_id=user.id,
            session_id="worker-test-session",
            title="Worker test",
        )
        db.add(conversation)
        db.flush()

        request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements={},
        )
        db.add(request)
        db.flush()

        plan = TerraformPlanRecord(
            infrastructure_request_id=request.id,
            requester_id=user.id,
            status="succeeded",
            plan_fingerprint="a" * 64,
            specification_fingerprint="b" * 64,
            plan_summary={},
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            requester_id=user.id,
            status="approved",
            plan_summary={},
            plan_fingerprint="a" * 64,
        )
        db.add(approval)
        db.flush()

        operation = InfrastructureOperation(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            approval_request_id=approval.id,
            requester_id=user.id,
            operation="provision",
            status="blocked",
        )
        db.add(operation)
        db.commit()

        operation_id = operation.id

    finally:
        db.close()

    result = tasks.execute_provision_operation.run(operation_id)

    assert result["status"] == "blocked"
    assert result["operation_id"] == operation_id
    assert result["message"] == "Operation is not queued."


@pytest.mark.parametrize(
    "fingerprint_matches_artifact,lock_artifact_mode",
    [
        (True, "valid"),
        (True, "missing"),
        (True, "corrupted"),
        (False, "valid"),
    ],
)
def test_worker_validates_plan_artifact_fingerprint(
    worker_db,
    fingerprint_matches_artifact,
    lock_artifact_mode,
    monkeypatch,
    tmp_path,
):
    requirements = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
    }

    specification = ValidatedInfrastructureSpecification.model_validate(
        requirements
    )

    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    specification_fingerprint = hashlib.sha256(
        canonical
    ).hexdigest()

    artifact_path, artifact_checksum = save_plan_artifact(
        b"valid-test-terraform-plan"
    )

    provider_lock_path, provider_lock_checksum = (
        save_provider_lock_artifact(
            b'# Test provider lock file\n'
        )
    )

    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform {\n  required_version = ">= 1.10.0"\n}\n'
        )
    )

    if lock_artifact_mode == "missing":
        provider_lock_path = None
        provider_lock_checksum = None
    elif lock_artifact_mode == "corrupted":
        provider_lock_checksum = "a" * 64

    plan_fingerprint = (
        artifact_checksum
        if fingerprint_matches_artifact
        else "a" * 64
    )

    db = worker_db()

    try:
        user = User(
            email="preflight@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()

        conversation = Conversation(
            user_id=user.id,
            session_id="preflight-test-session",
            title="Preflight test",
        )
        db.add(conversation)
        db.flush()

        request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements=requirements,
        )
        db.add(request)
        db.flush()

        plan = TerraformPlanRecord(
            infrastructure_request_id=request.id,
            requester_id=user.id,
            status="succeeded",
            plan_fingerprint=plan_fingerprint,
            specification_fingerprint=specification_fingerprint,
            plan_summary={},
            artifact_path=artifact_path,
            artifact_sha256=artifact_checksum,
            provider_lock_path=provider_lock_path,
            provider_lock_sha256=provider_lock_checksum,
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256=configuration_checksum,
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            requester_id=user.id,
            status="approved",
            plan_summary={},
            plan_fingerprint=plan_fingerprint,
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

        operation = InfrastructureOperation(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            approval_request_id=approval.id,
            requester_id=user.id,
            operation="provision",
            status="queued",
        )
        db.add(operation)
        db.commit()

        operation_id = operation.id

    finally:
        db.close()

    initialization_calls = {}

    def fake_build_environment():
        return {
            "PATH": os.environ.get("PATH", ""),
            "TF_INPUT": "0",
            "TF_IN_AUTOMATION": "1",
        }, tmp_path

    @contextmanager
    def fake_reconstructed_workspace(**kwargs):
        initialization_calls["workspace_artifacts"] = kwargs
        yield tmp_path / "fake-terraform-workspace"

    def fake_initialize_workspace(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        initialization_calls["initialization"] = {
            "workspace": workspace,
            "timeout_seconds": timeout_seconds,
            "env": env,
        }
        initialization_calls["lease_renewed"] = (
            lease_renewal_callback()
        )

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        fake_build_environment,
    )
    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        fake_reconstructed_workspace,
    )
    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        fake_initialize_workspace,
    )

    try:
        # First execution: worker claims the queued operation.
        result = tasks.execute_provision_operation.run(operation_id)

        assert result["status"] == "blocked", result
        assert result["operation_id"] == operation_id
        assert result["terraform_apply_enabled"] is False

        if not fingerprint_matches_artifact:
            expected_error_code = (
                "PLAN_ARTIFACT_FINGERPRINT_MISMATCH"
            )
        elif lock_artifact_mode == "missing":
            expected_error_code = "PROVIDER_LOCK_ARTIFACT_MISSING"
        elif lock_artifact_mode == "corrupted":
            expected_error_code = "PROVIDER_LOCK_ARTIFACT_INVALID"
        else:
            assert result["preflight_passed"] is True
            expected_error_code = "TERRAFORM_APPLY_DISABLED"

            assert "workspace_artifacts" in initialization_calls
            assert "initialization" in initialization_calls

            reconstructed = initialization_calls["workspace_artifacts"]
            assert reconstructed["configuration_artifact_path"] == (
                configuration_path
            )
            assert reconstructed["configuration_artifact_sha256"] == (
                configuration_checksum
            )
            assert reconstructed["provider_lock_path"] == (
                provider_lock_path
            )
            assert reconstructed["provider_lock_sha256"] == (
                provider_lock_checksum
            )
            assert reconstructed["plan_artifact_path"] == artifact_path
            assert reconstructed["plan_artifact_sha256"] == artifact_checksum
            assert reconstructed["temp_root"] == tmp_path

            initialized = initialization_calls["initialization"]
            assert initialized["workspace"] == (
                tmp_path / "fake-terraform-workspace"
            )
            assert initialized["timeout_seconds"] == 180
            assert initialized["env"]["TF_INPUT"] == "0"
            assert initialization_calls["lease_renewed"] is True

        if expected_error_code != "TERRAFORM_APPLY_DISABLED":
            assert result["error_code"] == expected_error_code

        # Second execution: duplicate task delivery must not claim it again.
        duplicate_result = tasks.execute_provision_operation.run(
            operation_id
        )

        assert duplicate_result["status"] == "blocked"
        assert duplicate_result["operation_id"] == operation_id
        assert duplicate_result["message"] == "Operation is not queued."

        verification_db = worker_db()
        try:
            saved_operation = verification_db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert saved_operation is not None
            assert saved_operation.status == "blocked"
            assert saved_operation.error_code == expected_error_code
            assert (
                saved_operation.result_summary["terraform_apply_enabled"]
                is False
            )

            if expected_error_code == "TERRAFORM_APPLY_DISABLED":
                assert (
                    saved_operation.result_summary["preflight_passed"]
                    is True
                )
            else:
                assert (
                    saved_operation.result_summary.get("preflight_passed")
                    is not True
                )

        finally:
            verification_db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_worker_marks_unexpected_preflight_exception_failed(
    worker_db,
    monkeypatch,
):
    requirements = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
    }

    specification = ValidatedInfrastructureSpecification.model_validate(
        requirements
    )

    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    specification_fingerprint = hashlib.sha256(
        canonical
    ).hexdigest()

    artifact_path, artifact_checksum = save_plan_artifact(
        b"valid-test-terraform-plan"
    )

    db = worker_db()

    try:
        user = User(
            email="worker-failure@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()

        conversation = Conversation(
            user_id=user.id,
            session_id="worker-failure-session",
            title="Worker failure test",
        )
        db.add(conversation)
        db.flush()

        request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements=requirements,
        )
        db.add(request)
        db.flush()

        plan = TerraformPlanRecord(
            infrastructure_request_id=request.id,
            requester_id=user.id,
            status="succeeded",
            plan_fingerprint="a" * 64,
            specification_fingerprint=specification_fingerprint,
            plan_summary={},
            artifact_path=artifact_path,
            artifact_sha256=artifact_checksum,
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            requester_id=user.id,
            status="approved",
            plan_summary={},
            plan_fingerprint="a" * 64,
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

        operation = InfrastructureOperation(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            approval_request_id=approval.id,
            requester_id=user.id,
            operation="provision",
            status="queued",
        )
        db.add(operation)
        db.commit()

        operation_id = operation.id

    finally:
        db.close()

    def raise_unexpected_error(specification):
        raise RuntimeError("Simulated preflight failure")

    monkeypatch.setattr(
        tasks,
        "_fingerprint_specification",
        raise_unexpected_error,
    )

    try:
        result = tasks.execute_provision_operation.run(operation_id)

        assert result["status"] == "failed"
        assert result["operation_id"] == operation_id
        assert result["error_code"] == "WORKER_PREFLIGHT_FAILED"

        db = worker_db()

        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "failed"
            assert operation.error_code == "WORKER_PREFLIGHT_FAILED"
            assert operation.execution_token is None
            assert operation.lease_expires_at is None
            assert operation.completed_at is not None

            event = db.scalar(
                select(InfrastructureOperationEvent).where(
                    InfrastructureOperationEvent.operation_id
                    == operation_id
                )
            )

            assert event is not None
            assert event.actor_type == "system"
            assert event.event_type == "worker_operation_finished"
            assert event.previous_status == "running"
            assert event.new_status == "failed"
            assert event.event_metadata["error_code"] == (
                "WORKER_PREFLIGHT_FAILED"
            )

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)

def create_recovery_test_operation(
    db,
    email: str,
    status: str,
    lease_expires_at=None,
    operation_type="provision",
):
    """Create a minimal operation for recovery tests."""

    user = User(
        email=email,
        password_hash="test-hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()

    conversation = Conversation(
        user_id=user.id,
        session_id=f"recovery-{email}",
        title="Recovery test",
    )
    db.add(conversation)
    db.flush()

    request = InfrastructureRequest(
        conversation_id=conversation.id,
        status="complete",
        requirements={},
    )
    db.add(request)
    db.flush()

    plan = TerraformPlanRecord(
        infrastructure_request_id=request.id,
        requester_id=user.id,
        status="succeeded",
        plan_fingerprint="a" * 64,
        specification_fingerprint="b" * 64,
        plan_summary={},
    )
    db.add(plan)
    db.flush()

    approval = ApprovalRequest(
        infrastructure_request_id=request.id,
        terraform_plan_record_id=plan.id,
        requester_id=user.id,
        status="approved",
        plan_summary={},
        plan_fingerprint="a" * 64,
    )
    db.add(approval)
    db.flush()

    operation = InfrastructureOperation(
        infrastructure_request_id=request.id,
        terraform_plan_record_id=plan.id,
        approval_request_id=approval.id,
        requester_id=user.id,
        operation=operation_type,
        status=status,
        execution_token=(
            "test-execution-token"
            if status == "running"
            else None
        ),
        lease_expires_at=lease_expires_at,
        started_at=(
            datetime.now(timezone.utc) - timedelta(minutes=20)
            if status == "running"
            else None
        ),
    )

    db.add(operation)
    db.commit()

    return operation.id


def test_recovery_marks_expired_operation_failed(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "expired-worker@example.com",
            "running",
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    finally:
        db.close()

    result = tasks.recover_stale_infrastructure_operations.run()

    assert result["status"] == "completed"
    assert result["recovered_count"] == 1
    assert operation_id in result["recovered_operation_ids"]
    assert result["automatic_retry"] is False

    db = worker_db()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        assert operation.status == "failed"
        assert operation.error_code == "WORKER_STALE_OPERATION"
        assert operation.execution_token is None
        assert operation.lease_expires_at is None
        assert operation.completed_at is not None

        assert operation.result_summary["manual_review_required"] is True
        assert operation.result_summary["terraform_apply_enabled"] is False

        event = db.scalar(
            select(InfrastructureOperationEvent).where(
                InfrastructureOperationEvent.operation_id == operation_id
            )
        )

        assert event is not None
        assert event.actor_type == "system"
        assert event.actor_id is None
        assert event.event_type == "worker_lease_expired"
        assert event.previous_status == "running"
        assert event.new_status == "failed"

    finally:
        db.close()


def test_recovery_ignores_recent_running_operation(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "recent-worker@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=10),
        )
    finally:
        db.close()

    result = tasks.recover_stale_infrastructure_operations.run()

    assert result["recovered_count"] == 0

    db = worker_db()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"

    finally:
        db.close()


def test_recovery_does_not_modify_queued_operation(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "queued-worker@example.com",
            "queued",
        )
    finally:
        db.close()

    result = tasks.recover_stale_infrastructure_operations.run()

    assert result["recovered_count"] == 0

    db = worker_db()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        assert operation.status == "queued"
        assert operation.error_code is None

    finally:
        db.close()


def test_recovery_is_idempotent(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "idempotent-worker@example.com",
            "running",
            datetime.now(timezone.utc) - timedelta(minutes=10),
        )
    finally:
        db.close()

    first_result = (
        tasks.recover_stale_infrastructure_operations.run()
    )
    second_result = (
        tasks.recover_stale_infrastructure_operations.run()
    )

    assert first_result["recovered_count"] == 1
    assert second_result["recovered_count"] == 0

    db = worker_db()

    try:
        events = list(
            db.scalars(
                select(InfrastructureOperationEvent).where(
                    InfrastructureOperationEvent.operation_id
                    == operation_id
                )
            ).all()
        )

        assert len(events) == 1

    finally:
        db.close()


def test_recovery_rejects_invalid_grace_period(worker_db):
    with pytest.raises(ValueError):
        tasks.recover_stale_infrastructure_operations.run(-1)

    with pytest.raises(ValueError):
        tasks.recover_stale_infrastructure_operations.run(1441)

    with pytest.raises(ValueError):
        tasks.recover_stale_infrastructure_operations.run(True)


def test_lease_renewal_succeeds_for_current_worker(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "lease-renewal@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        operation = db.get(InfrastructureOperation, operation_id)
        previous_expiry = operation.lease_expires_at

    finally:
        db.close()

    renewed = tasks._renew_operation_lease(
        operation_id,
        "test-execution-token",
    )

    assert renewed is True

    db = worker_db()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"
        assert operation.lease_expires_at > previous_expiry

    finally:
        db.close()


def test_lease_renewal_rejects_wrong_execution_token(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "wrong-token@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        operation = db.get(InfrastructureOperation, operation_id)
        previous_expiry = operation.lease_expires_at

    finally:
        db.close()

    renewed = tasks._renew_operation_lease(
        operation_id,
        "incorrect-token",
    )

    assert renewed is False

    db = worker_db()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"
        assert operation.lease_expires_at == previous_expiry

    finally:
        db.close()


def test_lease_renewal_rejects_non_running_operation(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "terminal-operation@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )

        operation = db.get(InfrastructureOperation, operation_id)
        operation.status = "blocked"
        db.commit()

    finally:
        db.close()

    renewed = tasks._renew_operation_lease(
        operation_id,
        "test-execution-token",
    )

    assert renewed is False


def test_lease_renewal_rejects_invalid_duration():
    with pytest.raises(ValueError):
        tasks._renew_operation_lease(1, "test-token", 0)

    with pytest.raises(ValueError):
        tasks._renew_operation_lease(1, "test-token", -1)

    with pytest.raises(ValueError):
        tasks._renew_operation_lease(1, "test-token", True)

def test_lease_renewal_rejects_expired_lease(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "expired-lease@example.com",
            "running",
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    finally:
        db.close()

    renewed = tasks._renew_operation_lease(
        operation_id,
        "test-execution-token",
    )

    assert renewed is False


def test_worker_lease_callback_renews_current_operation(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "heartbeat@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    finally:
        db.close()

    renewed = tasks._renew_operation_lease(
        operation_id,
        "test-execution-token",
    )

    assert renewed is True

    db = worker_db()
    try:
        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"

        lease_expires_at = operation.lease_expires_at

        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)

        assert lease_expires_at > (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        )
    finally:
        db.close()


def test_progress_event_records_for_current_worker(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "progress-success@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        recorded = tasks._record_operation_progress(
            db,
            operation_id,
            "test-execution-token",
            "validating_request",
            "Validating infrastructure request.",
        )

        assert recorded is True

        event = db.scalar(
            select(InfrastructureOperationEvent).where(
                InfrastructureOperationEvent.operation_id == operation_id
            )
        )

        assert event is not None
        assert event.event_type == "worker_progress"
        assert event.previous_status == "running"
        assert event.new_status == "running"
        assert event.event_metadata["stage"] == "validating_request"

    finally:
        db.close()


def test_progress_event_rejects_wrong_execution_token(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "progress-wrong-token@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        recorded = tasks._record_operation_progress(
            db,
            operation_id,
            "incorrect-token",
            "validating_request",
            "Validating infrastructure request.",
        )

        assert recorded is False

        event = db.scalar(
            select(InfrastructureOperationEvent).where(
                InfrastructureOperationEvent.operation_id == operation_id
            )
        )

        assert event is None

    finally:
        db.close()


def test_progress_event_rejects_expired_lease(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "progress-expired@example.com",
            "running",
            datetime.now(timezone.utc) - timedelta(minutes=5),
        )

        recorded = tasks._record_operation_progress(
            db,
            operation_id,
            "test-execution-token",
            "validating_request",
            "Validating infrastructure request.",
        )

        assert recorded is False

        event = db.scalar(
            select(InfrastructureOperationEvent).where(
                InfrastructureOperationEvent.operation_id == operation_id
            )
        )

        assert event is None

    finally:
        db.close()

def test_worker_process_integration_renews_lease(worker_db):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "process-integration@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    finally:
        db.close()

    result = tasks._run_owned_operation_process(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(0.4)",
        ],
        operation_id=operation_id,
        execution_token="test-execution-token",
        cwd=Path.cwd(),
        timeout_seconds=5,
        env=os.environ.copy(),
        lease_renewal_interval_seconds=0.1,
    )

    assert result.returncode == 0

    db = worker_db()

    try:
        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"

        lease_expires_at = operation.lease_expires_at

        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(
                tzinfo=timezone.utc
            )

        assert lease_expires_at > (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        )

    finally:
        db.close()

def test_worker_process_terminates_when_lease_is_lost(
    worker_db,
    tmp_path,
):
    marker_file = tmp_path / "process_survived.txt"

    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "lease-loss@example.com",
            "running",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
    finally:
        db.close()

    command = [
        sys.executable,
        "-c",
        (
            "import time; "
            "from pathlib import Path; "
            "time.sleep(1); "
            f"Path({str(marker_file)!r}).write_text('survived')"
        ),
    ]

    with pytest.raises(TerraformExecutionError) as exc_info:
        tasks._run_owned_operation_process(
            command,
            operation_id=operation_id,
            execution_token="incorrect-token",
            cwd=Path.cwd(),
            timeout_seconds=5,
            env=os.environ.copy(),
            lease_renewal_interval_seconds=0.1,
        )

    assert exc_info.value.code == "WORKER_LEASE_LOST"

    time.sleep(1.2)

    assert not marker_file.exists()

    db = worker_db()

    try:
        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        assert operation.status == "running"
        assert operation.execution_token == "test-execution-token"

    finally:
        db.close()


def create_deprovision_test_operation(db):
    """Create a fully approved deprovision operation for worker tests."""

    requirements = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
    }

    specification = ValidatedInfrastructureSpecification.model_validate(
        requirements
    )

    specification_fingerprint = tasks._fingerprint_specification(
        specification
    )

    artifact_path, artifact_checksum = save_plan_artifact(
        b"valid-test-destroy-plan"
    )

    provider_lock_path, provider_lock_checksum = (
        save_provider_lock_artifact(
            b'# Test provider lock file\n'
        )
    )

    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform {\n  required_version = ">= 1.10.0"\n}\n'
        )
    )

    user = User(
        email="deprovision-worker@example.com",
        password_hash="test-hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()

    conversation = Conversation(
        user_id=user.id,
        session_id="deprovision-worker-session",
        title="Deprovision worker test",
    )
    db.add(conversation)
    db.flush()

    request = InfrastructureRequest(
        conversation_id=conversation.id,
        status="complete",
        requirements=requirements,
    )
    db.add(request)
    db.flush()

    state_snapshot = {
        "version_id": "test-state-version-001",
        "serial": 3,
        "lineage": "12345678-1234-4234-8234-123456789abc",
        "sha256": "c" * 64,
    }

    plan = TerraformPlanRecord(
        infrastructure_request_id=request.id,
        requester_id=user.id,
        operation="deprovision",
        status="succeeded",
        plan_fingerprint=artifact_checksum,
        specification_fingerprint=specification_fingerprint,
        plan_summary={},
        artifact_path=artifact_path,
        artifact_sha256=artifact_checksum,
        provider_lock_path=provider_lock_path,
        provider_lock_sha256=provider_lock_checksum,
        configuration_artifact_path=configuration_path,
        configuration_artifact_sha256=configuration_checksum,
        state_version_id=state_snapshot["version_id"],
        state_serial=state_snapshot["serial"],
        state_lineage=state_snapshot["lineage"],
        state_sha256=state_snapshot["sha256"],
    )
    db.add(plan)
    db.flush()

    approval = ApprovalRequest(
        infrastructure_request_id=request.id,
        terraform_plan_record_id=plan.id,
        requester_id=user.id,
        status="approved",
        plan_summary={},
        plan_fingerprint=artifact_checksum,
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

    operation = InfrastructureOperation(
        infrastructure_request_id=request.id,
        terraform_plan_record_id=plan.id,
        approval_request_id=approval.id,
        requester_id=user.id,
        operation="deprovision",
        status="queued",
        result_summary={
            "destroy_confirmed": True,
            "terraform_destroy_enabled": False,
        },
    )
    db.add(operation)
    db.commit()

    return (
        operation.id,
        state_snapshot,
        artifact_path,
        provider_lock_path,
        configuration_path,
    )

def test_provision_worker_does_not_claim_deprovision_operation(
    worker_db,
):
    db = worker_db()

    try:
        (
            operation_id,
            _,
            _,
            _,
            _,
        ) = create_deprovision_test_operation(db)

    finally:
        db.close()

    result = tasks.execute_provision_operation.run(operation_id)

    assert result["status"] == "queued"
    assert result["message"] == "Operation is not queued."

    db = worker_db()

    try:
        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        assert operation is not None
        assert operation.operation == "deprovision"
        assert operation.status == "queued"
        assert operation.execution_token is None
        assert operation.lease_expires_at is None

    finally:
        db.close()

def test_deprovision_worker_completes_preflight_without_destroy(
    worker_db,
    monkeypatch,
    tmp_path,
):
    db = worker_db()

    try:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)
    finally:
        db.close()

    initialization_calls = {}

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    def fake_build_environment():
        return {
            "PATH": os.environ.get("PATH", ""),
            "TF_INPUT": "0",
            "TF_IN_AUTOMATION": "1",
        }, tmp_path

    @contextmanager
    def fake_reconstructed_workspace(**kwargs):
        initialization_calls["workspace_artifacts"] = kwargs
        yield tmp_path / "fake-destroy-workspace"

    def fake_initialize_workspace(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        initialization_calls["initialization"] = {
            "workspace": workspace,
            "timeout_seconds": timeout_seconds,
            "env": env,
        }
        initialization_calls["lease_renewed"] = (
            lease_renewal_callback()
        )

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        fake_build_environment,
    )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        fake_reconstructed_workspace,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        fake_initialize_workspace,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked"
        assert result["operation_id"] == operation_id
        assert result["error_code"] == "TERRAFORM_DESTROY_DISABLED"
        assert result["preflight_passed"] is True
        assert result["terraform_apply_enabled"] is False

        assert "workspace_artifacts" in initialization_calls
        assert "initialization" in initialization_calls
        assert initialization_calls["lease_renewed"] is True

        reconstructed = initialization_calls["workspace_artifacts"]

        assert reconstructed["plan_artifact_path"] == artifact_path
        assert reconstructed["provider_lock_path"] == provider_lock_path
        assert reconstructed["configuration_artifact_path"] == (
            configuration_path
        )
        assert reconstructed["temp_root"] == tmp_path

        db = worker_db()
        try:
            saved_operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert saved_operation.status == "blocked"
            assert saved_operation.error_code == (
                "TERRAFORM_DESTROY_DISABLED"
            )
            assert saved_operation.result_summary["destroy_confirmed"] is True
            assert saved_operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert saved_operation.result_summary[
                "state_snapshot_verified"
            ] is True
            assert saved_operation.result_summary[
                "terraform_initialization_completed"
            ] is True

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_blocks_when_destroy_confirmation_is_missing(
    worker_db,
    monkeypatch,
):
    with worker_db() as db:
        (
            operation_id,
            _,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

        operation = db.get(InfrastructureOperation, operation_id)
        operation.result_summary = {
            "destroy_confirmed": False,
            "terraform_destroy_enabled": False,
        }
        db.commit()

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "Terraform preflight must not run without destroy confirmation."
        )

    monkeypatch.setattr(tasks, "get_state_record", unexpected_call)
    monkeypatch.setattr(tasks, "create_s3_client", unexpected_call)

    try:
        result = tasks.execute_deprovision_operation.run(operation_id)

        assert result["status"] == "blocked"
        assert result["error_code"] == "DESTROY_CONFIRMATION_MISSING"

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_blocks_when_state_record_is_missing(
    worker_db,
    monkeypatch,
):
    with worker_db() as db:
        (
            operation_id,
            _,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    def missing_state_record(db, request_id):
        return None

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "S3 verification or Terraform initialization must not run "
            "when the state record is missing."
        )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        missing_state_record,
    )
    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        unexpected_call,
    )
    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        unexpected_call,
    )
    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_call,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked"
        assert result["operation_id"] == operation_id
        assert result["error_code"] == "STATE_RECORD_MISSING"

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == "STATE_RECORD_MISSING"
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is False

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_blocks_when_state_snapshot_verification_fails(
    worker_db,
    monkeypatch,
):
    with worker_db() as db:
        (
            operation_id,
            _,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    s3_calls = {}

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    def fake_create_s3_client(region):
        s3_calls["region"] = region
        return object()

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        fake_create_s3_client,
    )

    def fail_state_verification(client, state_record):
        s3_calls["verification_called"] = True
        raise tasks.TerraformStateError(
            "Unable to verify active Terraform state."
        )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        fail_state_verification,
    )

    def unexpected_initialization(*args, **kwargs):
        pytest.fail(
            "Terraform initialization must not run when "
            "state snapshot verification fails."
        )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_initialization,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked"
        assert result["operation_id"] == operation_id
        assert result["error_code"] == (
            "STATE_SNAPSHOT_VERIFICATION_FAILED"
        )

        assert s3_calls["region"] == "ap-south-1"
        assert s3_calls["verification_called"] is True

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == (
                "STATE_SNAPSHOT_VERIFICATION_FAILED"
            )
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is False

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


@pytest.mark.parametrize(
    "field,mismatched_value",
    [
        ("version_id", "different-state-version"),
        ("serial", 999),
        ("lineage", "different-lineage"),
        ("sha256", "d" * 64),
    ],
)
def test_deprovision_worker_blocks_when_state_snapshot_mismatches_destroy_plan(
    worker_db,
    monkeypatch,
    field,
    mismatched_value,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    current_snapshot = dict(state_snapshot)
    current_snapshot[field] = mismatched_value

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: current_snapshot,
    )

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "Terraform workspace reconstruction or initialization "
            "must not run when the destroy plan state snapshot mismatches."
        )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        unexpected_call,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_call,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked"
        assert result["operation_id"] == operation_id
        assert result["error_code"] == (
            "DESTROY_PLAN_STATE_SNAPSHOT_MISMATCH"
        )

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == (
                "DESTROY_PLAN_STATE_SNAPSHOT_MISMATCH"
            )
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is False

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


@pytest.mark.parametrize(
    "artifact_failure,expected_error",
    [
        (
            "missing",
            "PLAN_ARTIFACT_MISSING",
        ),
        (
            "fingerprint_mismatch",
            "PLAN_ARTIFACT_FINGERPRINT_MISMATCH",
        ),
        (
            "corrupted",
            "PLAN_ARTIFACT_INVALID",
        ),
    ],
)
def test_deprovision_worker_blocks_when_destroy_plan_artifact_is_invalid(
    worker_db,
    monkeypatch,
    artifact_failure,
    expected_error,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

        plan = db.scalar(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.operation == "deprovision"
            )
        )

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        approval = db.get(
            ApprovalRequest,
            operation.approval_request_id,
        )

        if artifact_failure == "missing":
            plan.artifact_path = None
            plan.artifact_sha256 = None

        elif artifact_failure == "fingerprint_mismatch":
            plan.plan_fingerprint = "a" * 64
            approval.plan_fingerprint = "a" * 64

        db.commit()

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    if artifact_failure == "corrupted":
        def corrupted_plan_loader(*args, **kwargs):
            raise tasks.TerraformArtifactError(
                "Destroy plan artifact integrity verification failed."
            )

        monkeypatch.setattr(
            tasks,
            "load_plan_artifact",
            corrupted_plan_loader,
        )

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "Terraform workspace reconstruction or initialization "
            "must not run when the destroy plan artifact is invalid."
        )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        unexpected_call,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_call,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked", result
        assert result["operation_id"] == operation_id
        assert result["error_code"] == expected_error

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == expected_error
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is True

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


@pytest.mark.parametrize(
    "lock_failure,expected_error",
    [
        (
            "missing",
            "PROVIDER_LOCK_ARTIFACT_MISSING",
        ),
        (
            "corrupted",
            "PROVIDER_LOCK_ARTIFACT_INVALID",
        ),
    ],
)
def test_deprovision_worker_blocks_when_provider_lock_artifact_is_invalid(
    worker_db,
    monkeypatch,
    lock_failure,
    expected_error,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )
        plan = db.get(
            TerraformPlanRecord,
            operation.terraform_plan_record_id,
        )

        if lock_failure == "missing":
            plan.provider_lock_path = None
            plan.provider_lock_sha256 = None

        db.commit()

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    if lock_failure == "corrupted":
        def corrupted_lock_loader(*args, **kwargs):
            raise tasks.TerraformArtifactError(
                "Provider lock artifact integrity verification failed."
            )

        monkeypatch.setattr(
            tasks,
            "load_provider_lock_artifact",
            corrupted_lock_loader,
        )

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "Terraform workspace reconstruction or initialization "
            "must not run when the provider lock artifact is invalid."
        )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        unexpected_call,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_call,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked", result
        assert result["operation_id"] == operation_id
        assert result["error_code"] == expected_error

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == expected_error
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is True

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


@pytest.mark.parametrize(
    "configuration_failure,expected_error",
    [
        (
            "missing",
            "CONFIGURATION_ARTIFACT_MISSING",
        ),
        (
            "corrupted",
            "CONFIGURATION_ARTIFACT_INVALID",
        ),
    ],
)
def test_deprovision_worker_blocks_when_configuration_artifact_is_invalid(
    worker_db,
    monkeypatch,
    configuration_failure,
    expected_error,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )
        plan = db.get(
            TerraformPlanRecord,
            operation.terraform_plan_record_id,
        )

        if configuration_failure == "missing":
            plan.configuration_artifact_path = None
            plan.configuration_artifact_sha256 = None

        db.commit()

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    if configuration_failure == "corrupted":
        def corrupted_configuration_loader(*args, **kwargs):
            raise tasks.TerraformArtifactError(
                "Configuration artifact integrity verification failed."
            )

        monkeypatch.setattr(
            tasks,
            "load_configuration_artifact",
            corrupted_configuration_loader,
        )

    def unexpected_call(*args, **kwargs):
        pytest.fail(
            "Terraform workspace reconstruction or initialization "
            "must not run when the configuration artifact is invalid."
        )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        unexpected_call,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        unexpected_call,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "blocked", result
        assert result["operation_id"] == operation_id
        assert result["error_code"] == expected_error

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "blocked"
            assert operation.error_code == expected_error
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is True

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_fails_when_terraform_initialization_fails(
    worker_db,
    monkeypatch,
    tmp_path,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    initialization_calls = {}

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    def fake_build_environment():
        return {
            "PATH": os.environ.get("PATH", ""),
            "TF_INPUT": "0",
            "TF_IN_AUTOMATION": "1",
        }, tmp_path

    @contextmanager
    def fake_reconstructed_workspace(**kwargs):
        initialization_calls["workspace_reconstructed"] = True
        initialization_calls["workspace_artifacts"] = kwargs
        yield tmp_path / "fake-destroy-workspace"

    def fail_initialization(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        initialization_calls["initialization_called"] = True
        initialization_calls["timeout_seconds"] = timeout_seconds

        raise TerraformExecutionError(
            "TEST_TERRAFORM_INIT_FAILURE"
        )

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        fake_build_environment,
    )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        fake_reconstructed_workspace,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        fail_initialization,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "failed", result
        assert result["operation_id"] == operation_id
        assert result["error_code"] == (
            "TERRAFORM_INITIALIZATION_FAILED"
        )

        assert initialization_calls["workspace_reconstructed"] is True
        assert initialization_calls["initialization_called"] is True
        assert initialization_calls["timeout_seconds"] == 180

        reconstructed = initialization_calls["workspace_artifacts"]

        assert reconstructed["plan_artifact_path"] == artifact_path
        assert reconstructed["provider_lock_path"] == provider_lock_path
        assert reconstructed["configuration_artifact_path"] == (
            configuration_path
        )
        assert reconstructed["temp_root"] == tmp_path

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "failed"
            assert operation.error_code == (
                "TERRAFORM_INITIALIZATION_FAILED"
            )
            assert operation.result_summary["destroy_confirmed"] is True
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False
            assert operation.result_summary[
                "state_snapshot_verified"
            ] is True
            assert operation.result_summary[
                "terraform_initialization_completed"
            ] is False

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_handles_unexpected_preflight_exception(
    worker_db,
    monkeypatch,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        lambda client, state_record: state_snapshot,
    )

    def unexpected_environment_failure():
        raise RuntimeError(
            "Unexpected environment initialization failure."
        )

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        unexpected_environment_failure,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "failed", result
        assert result["operation_id"] == operation_id
        assert result["error_code"] == "DEPROVISION_WORKER_FAILED"

        db = worker_db()
        try:
            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert operation.status == "failed"
            assert operation.error_code == (
                "DEPROVISION_WORKER_FAILED"
            )
            assert operation.result_summary[
                "terraform_destroy_enabled"
            ] is False

        finally:
            db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_recovery_disables_destroy_for_stale_deprovision_operation(
    worker_db,
):
    db = worker_db()

    try:
        operation_id = create_recovery_test_operation(
            db,
            "stale-deprovision@example.com",
            "running",
            datetime.now(timezone.utc) - timedelta(minutes=10),
            operation_type="deprovision",
        )
    finally:
        db.close()

    result = tasks.recover_stale_infrastructure_operations.run()

    assert result["status"] == "completed"
    assert result["recovered_count"] == 1
    assert operation_id in result["recovered_operation_ids"]
    assert result["automatic_retry"] is False

    db = worker_db()

    try:
        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        assert operation.operation == "deprovision"
        assert operation.status == "failed"
        assert operation.error_code == "WORKER_STALE_OPERATION"

        assert operation.execution_token is None
        assert operation.lease_expires_at is None

        assert operation.result_summary["manual_review_required"] is True
        assert operation.result_summary["terraform_apply_enabled"] is False
        assert operation.result_summary["terraform_destroy_enabled"] is False

        event = db.scalar(
            select(InfrastructureOperationEvent).where(
                InfrastructureOperationEvent.operation_id == operation_id
            )
        )

        assert event is not None
        assert event.event_type == "worker_lease_expired"
        assert event.event_metadata["automatic_retry"] is False

    finally:
        db.close()

def _create_cleanup_scan_test_data(
    db,
    *,
    tags=None,
    dependencies=None,
    active_operation=False,
):
    user = User(
        email="cleanup-scan@example.com",
        password_hash="test-hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()

    conversation = Conversation(
        user_id=user.id,
        session_id="cleanup-scan-session",
        title="Cleanup scan test",
    )
    db.add(conversation)
    db.flush()

    request = InfrastructureRequest(
        conversation_id=conversation.id,
        status="complete",
        requirements={},
    )
    db.add(request)
    db.flush()

    resource = InfrastructureResource(
        infrastructure_request_id=request.id,
        resource_address="aws_instance.web",
        resource_type="aws_instance",
        resource_name="web",
        provider="provider[\"registry.terraform.io/hashicorp/aws\"]",
        mode="managed",
        module=None,
        instance_key=None,
        resource_id="i-cleanup123",
        tags=tags if tags is not None else {"AutoCleanup": "true"},
        dependencies=dependencies or [],
        instance_status="unknown",
        state_serial=1,
        is_present=True,
    )
    db.add(resource)

    if active_operation:
        plan = TerraformPlanRecord(
            infrastructure_request_id=request.id,
            requester_id=user.id,
            status="succeeded",
            plan_fingerprint="a" * 64,
            specification_fingerprint="b" * 64,
            plan_summary={},
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            requester_id=user.id,
            status="approved",
            plan_summary={},
            plan_fingerprint="a" * 64,
        )
        db.add(approval)
        db.flush()

        db.add(
            InfrastructureOperation(
                infrastructure_request_id=request.id,
                terraform_plan_record_id=plan.id,
                approval_request_id=approval.id,
                requester_id=user.id,
                operation="provision",
                status="running",
            )
        )

    db.commit()
    return request.id


def test_cleanup_scan_includes_explicitly_opted_in_ec2(worker_db):
    db = worker_db()

    try:
        _create_cleanup_scan_test_data(db)

    finally:
        db.close()

    result = tasks.scan_scheduled_cleanup_candidates.run()

    assert result["status"] == "completed"
    assert result["mode"] == "read_only"
    assert result["terraform_destroy_enabled"] is False
    assert result["scanned_resources"] == 1
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["resource_address"] == "aws_instance.web"
    assert result["candidates"][0]["resource_id"] == "i-cleanup123"


def test_cleanup_scan_excludes_resource_without_opt_in(worker_db):
    db = worker_db()

    try:
        _create_cleanup_scan_test_data(
            db,
            tags={"Name": "web"},
        )

    finally:
        db.close()

    result = tasks.scan_scheduled_cleanup_candidates.run()

    assert result["candidate_count"] == 0
    assert result["skipped_without_opt_in"] == 1


def test_cleanup_scan_excludes_resource_used_by_another_resource(worker_db):
    db = worker_db()

    try:
        request_id = _create_cleanup_scan_test_data(db)

        db.add(
            InfrastructureResource(
                infrastructure_request_id=request_id,
                resource_address="aws_security_group.web",
                resource_type="aws_security_group",
                resource_name="web",
                provider="provider[\"registry.terraform.io/hashicorp/aws\"]",
                mode="managed",
                module=None,
                instance_key=None,
                resource_id="sg-cleanup123",
                tags={},
                dependencies=["aws_instance.web"],
                instance_status="unknown",
                state_serial=1,
                is_present=True,
            )
        )
        db.commit()

    finally:
        db.close()

    result = tasks.scan_scheduled_cleanup_candidates.run()

    assert result["candidate_count"] == 0
    assert result["skipped_dependencies"] == 1


def test_cleanup_scan_excludes_resource_with_active_operation(worker_db):
    db = worker_db()

    try:
        _create_cleanup_scan_test_data(
            db,
            active_operation=True,
        )

    finally:
        db.close()

    result = tasks.scan_scheduled_cleanup_candidates.run()

    assert result["candidate_count"] == 0
    assert result["skipped_active_operations"] == 1

def test_provision_worker_executes_saved_plan_when_feature_flag_enabled(
    worker_db,
    monkeypatch,
    tmp_path,
):
    requirements = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
    }

    specification = ValidatedInfrastructureSpecification.model_validate(
        requirements
    )
    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    specification_fingerprint = hashlib.sha256(canonical).hexdigest()

    artifact_path, artifact_checksum = save_plan_artifact(
        b"valid-test-terraform-plan"
    )
    provider_lock_path, provider_lock_checksum = (
        save_provider_lock_artifact(b'# Test provider lock file\n')
    )
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform {\n  required_version = ">= 1.10.0"\n}\n'
        )
    )

    db = worker_db()

    try:
        user = User(
            email="apply-enabled@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()

        conversation = Conversation(
            user_id=user.id,
            session_id="apply-enabled-session",
            title="Apply enabled test",
        )
        db.add(conversation)
        db.flush()

        request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="complete",
            requirements=requirements,
        )
        db.add(request)
        db.flush()

        plan = TerraformPlanRecord(
            infrastructure_request_id=request.id,
            requester_id=user.id,
            status="succeeded",
            plan_fingerprint=artifact_checksum,
            specification_fingerprint=specification_fingerprint,
            plan_summary={},
            artifact_path=artifact_path,
            artifact_sha256=artifact_checksum,
            provider_lock_path=provider_lock_path,
            provider_lock_sha256=provider_lock_checksum,
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256=configuration_checksum,
        )
        db.add(plan)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            requester_id=user.id,
            status="approved",
            plan_summary={},
            plan_fingerprint=artifact_checksum,
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

        operation = InfrastructureOperation(
            infrastructure_request_id=request.id,
            terraform_plan_record_id=plan.id,
            approval_request_id=approval.id,
            requester_id=user.id,
            operation="provision",
            status="queued",
        )
        db.add(operation)
        db.commit()
        operation_id = operation.id
        request_id = request.id

    finally:
        db.close()

    monkeypatch.setenv("AIOPS_TERRAFORM_APPLY_ENABLED", "true")

    calls = {}

    def fake_build_environment():
        return {
            "PATH": os.environ.get("PATH", ""),
            "TF_INPUT": "0",
            "TF_IN_AUTOMATION": "1",
        }, tmp_path

    @contextmanager
    def fake_reconstructed_workspace(**kwargs):
        calls["workspace_artifacts"] = kwargs
        yield tmp_path / "fake-apply-workspace"

    def fake_initialize_workspace(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        calls["initialized"] = True
        assert lease_renewal_callback() is True

    def fake_execute_saved_plan(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        calls["apply"] = {
            "workspace": workspace,
            "timeout_seconds": timeout_seconds,
        }

    def fake_sync_state(db, infrastructure_request_id):
        calls["reconciled_request_id"] = infrastructure_request_id
        return {
            "state_reconciled": True,
            "state_serial": 4,
            "inventory": {"present_resources": 1},
        }

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        fake_build_environment,
    )
    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        fake_reconstructed_workspace,
    )
    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        fake_initialize_workspace,
    )
    monkeypatch.setattr(
        tasks,
        "execute_terraform_saved_plan",
        fake_execute_saved_plan,
    )
    monkeypatch.setattr(
        tasks,
        "_sync_state_after_execution",
        fake_sync_state,
    )

    try:
        result = tasks.execute_provision_operation.run(operation_id)

        assert result["status"] == "succeeded", result
        assert result["terraform_apply_enabled"] is True
        assert result["terraform_apply_executed"] is True
        assert result["deployment_succeeded"] is True
        assert result["state_reconciled"] is True

        assert calls["initialized"] is True
        assert calls["apply"]["workspace"] == (
            tmp_path / "fake-apply-workspace"
        )
        assert calls["apply"]["timeout_seconds"] == 600
        assert calls["reconciled_request_id"] == request_id

        verification_db = worker_db()
        try:
            saved_operation = verification_db.get(
                InfrastructureOperation,
                operation_id,
            )
            assert saved_operation.status == "succeeded"
            assert saved_operation.result_summary[
                "terraform_apply_executed"
            ] is True
            assert saved_operation.result_summary[
                "state_reconciled"
            ] is True
        finally:
            verification_db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)


def test_deprovision_worker_executes_saved_destroy_plan_when_feature_flag_enabled(
    worker_db,
    monkeypatch,
    tmp_path,
):
    with worker_db() as db:
        (
            operation_id,
            state_snapshot,
            artifact_path,
            provider_lock_path,
            configuration_path,
        ) = create_deprovision_test_operation(db)

    monkeypatch.setenv("AIOPS_TERRAFORM_DESTROY_ENABLED", "true")

    calls = {
        "state_verifications": 0,
    }

    fake_state_record = SimpleNamespace(
        state_region="ap-south-1",
    )

    monkeypatch.setattr(
        tasks,
        "get_state_record",
        lambda db, request_id: fake_state_record,
    )

    monkeypatch.setattr(
        tasks,
        "create_s3_client",
        lambda region: object(),
    )

    def fake_verify_state_snapshot(client, state_record):
        calls["state_verifications"] += 1
        return state_snapshot

    monkeypatch.setattr(
        tasks,
        "verify_active_state_snapshot",
        fake_verify_state_snapshot,
    )

    def fake_build_environment():
        return {
            "PATH": os.environ.get("PATH", ""),
            "TF_INPUT": "0",
            "TF_IN_AUTOMATION": "1",
        }, tmp_path

    @contextmanager
    def fake_reconstructed_workspace(**kwargs):
        calls["workspace_artifacts"] = kwargs
        yield tmp_path / "fake-destroy-workspace"

    def fake_initialize_workspace(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        calls["initialized"] = True
        calls["initialization_timeout"] = timeout_seconds
        assert lease_renewal_callback() is True

    def fake_execute_saved_plan(
        workspace,
        *,
        timeout_seconds,
        env,
        lease_renewal_callback,
    ):
        calls["destroy"] = {
            "workspace": workspace,
            "timeout_seconds": timeout_seconds,
        }
        assert lease_renewal_callback() is True

    def fake_sync_state(db, infrastructure_request_id):
        calls["reconciled_request_id"] = infrastructure_request_id
        return {
            "state_reconciled": True,
            "state_serial": 4,
            "inventory": {
                "present_resources": 0,
            },
        }

    monkeypatch.setattr(
        tasks,
        "build_terraform_environment",
        fake_build_environment,
    )

    monkeypatch.setattr(
        tasks,
        "reconstructed_terraform_workspace",
        fake_reconstructed_workspace,
    )

    monkeypatch.setattr(
        tasks,
        "initialize_terraform_apply_workspace",
        fake_initialize_workspace,
    )

    monkeypatch.setattr(
        tasks,
        "execute_terraform_saved_plan",
        fake_execute_saved_plan,
    )

    monkeypatch.setattr(
        tasks,
        "_sync_state_after_execution",
        fake_sync_state,
    )

    try:
        result = tasks.execute_deprovision_operation.run(
            operation_id
        )

        assert result["status"] == "succeeded", result
        assert result["terraform_destroy_enabled"] is True
        assert result["terraform_destroy_executed"] is True
        assert result["state_reconciled"] is True

        # State must be verified before preflight and again
        # immediately before executing the approved Destroy plan.
        assert calls["state_verifications"] == 2

        assert calls["initialized"] is True
        assert calls["initialization_timeout"] == 180

        assert calls["destroy"]["workspace"] == (
            tmp_path / "fake-destroy-workspace"
        )
        assert calls["destroy"]["timeout_seconds"] == 600

        reconstructed = calls["workspace_artifacts"]

        assert reconstructed["plan_artifact_path"] == artifact_path
        assert reconstructed["provider_lock_path"] == provider_lock_path
        assert reconstructed["configuration_artifact_path"] == (
            configuration_path
        )
        assert reconstructed["temp_root"] == tmp_path

        verification_db = worker_db()
        try:
            saved_operation = verification_db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert saved_operation is not None
            assert saved_operation.status == "succeeded"
            assert saved_operation.result_summary[
                "terraform_destroy_enabled"
            ] is True
            assert saved_operation.result_summary[
                "terraform_destroy_executed"
            ] is True
            assert saved_operation.result_summary[
                "state_reconciled"
            ] is True

        finally:
            verification_db.close()

    finally:
        delete_plan_artifact(artifact_path)
        delete_provider_lock_artifact(provider_lock_path)
        delete_configuration_artifact(configuration_path)
