
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
        operation="provision",
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