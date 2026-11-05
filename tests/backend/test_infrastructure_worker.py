
import pytest
import hashlib
import json

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    save_plan_artifact,
    delete_plan_artifact,
)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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

def test_worker_passes_preflight_but_keeps_apply_disabled(worker_db):
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

    plan_fingerprint = "a" * 64

    artifact_path, artifact_checksum = save_plan_artifact(
        b"valid-test-terraform-plan"
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

    try:
        result = tasks.execute_provision_operation.run(operation_id)

        assert result["status"] == "blocked"
        assert result["operation_id"] == operation_id
        assert result["preflight_passed"] is True
        assert result["terraform_apply_enabled"] is False

        verification_db = worker_db()
        try:
            saved_operation = verification_db.get(
                InfrastructureOperation,
                operation_id,
            )

            assert saved_operation is not None
            assert saved_operation.status == "blocked"
            assert (
                saved_operation.error_code
                == "TERRAFORM_APPLY_DISABLED"
            )
            assert saved_operation.result_summary["preflight_passed"] is True
            assert (
                saved_operation.result_summary["terraform_apply_enabled"]
                is False
            )
        finally:
            verification_db.close()

    finally:
        delete_plan_artifact(artifact_path)
