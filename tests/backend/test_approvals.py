import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.models.user import User
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    delete_plan_artifact,
    save_plan_artifact,
)


# Isolated in-memory database for tests only.
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
    artifact_paths = []
    db = TestingSessionLocal()

    try:
        requester = User(
            email="requester@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        reviewer = User(
            email="reviewer@example.com",
            password_hash="test-hash",
            role="admin",
            is_active=True,
        )

        db.add_all([requester, reviewer])
        db.flush()

        conversation = Conversation(
            user_id=requester.id,
            session_id="approval-test-session",
            title="Approval test",
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
            },
        )
        db.add(infra_request)
        db.commit()

        setup_data = {
            "requester_id": requester.id,
            "reviewer_id": reviewer.id,
            "infra_request_id": infra_request.id,
            "artifact_paths": artifact_paths,
            "requester_headers": {
                "Authorization": (
                    f"Bearer {create_access_token(str(requester.id))}"
                )
            },
            "reviewer_headers": {
                "Authorization": (
                    f"Bearer {create_access_token(str(reviewer.id))}"
                )
            },
        }
    finally:
        db.close()

    try:
        yield setup_data
    finally:
        for artifact_path in artifact_paths:
            try:
                delete_plan_artifact(artifact_path)
            except TerraformArtifactError:
                # A test may deliberately remove or invalidate an artifact.
                pass


@pytest.fixture
def valid_plan():
    return {
        "format_version": "1.2",
        "terraform_version": "1.15.6",
        "errored": False,
        "complete": True,
        "applyable": True,
        "resource_changes": [
            {
                "address": "aws_instance.example",
                "mode": "managed",
                "type": "aws_instance",
                "name": "example",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {
                        "instance_type": "t3.medium",
                        "password": "test-secret",
                    },
                    "after_unknown": {},
                    "before_sensitive": {},
                    "after_sensitive": {"password": True},
                },
            }
        ],
            "security_analysis": {
            "policy_version": "initial-1",
            "checks_completed": True,
            "deployment_enabled": False,
            "passed": True,
            "finding_count": 0,
            "severity_counts": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            },
            "findings": [],
        },
    }


def _fingerprint_specification(requirements):
    """Match the production specification fingerprint format."""
    specification = ValidatedInfrastructureSpecification.model_validate(
        requirements
    )
    canonical_specification = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(
        canonical_specification.encode("utf-8")
    ).hexdigest()


def _create_test_plan_artifact(setup_data):
    """Create and register a private test-only binary plan artifact."""
    artifact_path, artifact_checksum = save_plan_artifact(
        b"test-only-terraform-plan-artifact"
    )
    setup_data["artifact_paths"].append(artifact_path)
    return artifact_path, artifact_checksum


def create_trusted_plan_record(setup_data, plan=None):
    """
    Create a successful test plan record with a verified test artifact.

    This is test-only database setup. It does not run Terraform or
    prove that the fixture represents a real cloud plan.
    """
    if plan is None:
        plan = {
            "preview_type": "test_fixture",
            "resources": [],
        }

    artifact_path, artifact_checksum = _create_test_plan_artifact(setup_data)

    db = TestingSessionLocal()
    try:
        infra_request = db.get(
            InfrastructureRequest,
            setup_data["infra_request_id"],
        )
        assert infra_request is not None

        plan_record = TerraformPlanRecord(
            infrastructure_request_id=infra_request.id,
            requester_id=setup_data["requester_id"],
            status="succeeded",
            plan_fingerprint=artifact_checksum,
            specification_fingerprint=_fingerprint_specification(
                infra_request.requirements
            ),
            plan_summary=plan,
            artifact_path=artifact_path,
            artifact_sha256=artifact_checksum,
        )
        db.add(plan_record)
        db.commit()
        db.refresh(plan_record)

        return plan_record.id
    finally:
        db.close()


def create_pending_approval(setup_data, plan=None):
    """
    Create a linked plan record, pending approval, and audit event.

    This helper is only for test setup. It does not bypass the production
    API security safeguard or create real cloud infrastructure.
    """
    if plan is None:
        plan = {
            "preview_type": "test_fixture",
            "resources": [],
        }

    artifact_path, artifact_checksum = _create_test_plan_artifact(setup_data)

    db = TestingSessionLocal()
    try:
        infra_request = db.get(
            InfrastructureRequest,
            setup_data["infra_request_id"],
        )
        assert infra_request is not None

        plan_record = TerraformPlanRecord(
            infrastructure_request_id=infra_request.id,
            requester_id=setup_data["requester_id"],
            status="succeeded",
            plan_fingerprint=artifact_checksum,
            specification_fingerprint=_fingerprint_specification(
                infra_request.requirements
            ),
            plan_summary=plan,
            artifact_path=artifact_path,
            artifact_sha256=artifact_checksum,
        )
        db.add(plan_record)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=infra_request.id,
            terraform_plan_record_id=plan_record.id,
            requester_id=setup_data["requester_id"],
            status="pending",
            plan_summary=plan,
            plan_fingerprint=artifact_checksum,
        )
        db.add(approval)
        db.flush()

        for stage in ("technical", "cost", "security"):
            db.add(
                ApprovalStageReview(
                    approval_request_id=approval.id,
                    stage=stage,
                    status="pending",
                )
            )

        db.add(
            ApprovalAuditEvent(
                approval_request_id=approval.id,
                actor_id=setup_data["requester_id"],
                event_type="submitted",
                previous_status=None,
                new_status="pending",
                comments=None,
                event_metadata={
                    "source": "test_fixture",
                    "terraform_plan_record_id": plan_record.id,
                },
            )
        )

        db.commit()
        db.refresh(approval)
        return approval.id
    finally:
        db.close()


def approve_all_stages(client, setup_data, approval_id):
    """Complete all three review stages before final approval."""

    headers = setup_data["reviewer_headers"]

    # Technical review
    response = client.post(
        f"/approvals/{approval_id}/stages/technical/decision",
        headers=headers,
        json={
            "decision": "approved",
            "comments": "Technical configuration reviewed.",
        },
    )
    assert response.status_code == 200, response.text

    # The test fixture has no verified cost estimate, so require
    # an explicit, documented manual override for this test only.
    response = client.post(
        f"/approvals/{approval_id}/stages/cost/decision",
        headers=headers,
        json={
            "decision": "approved",
            "comments": "Manual cost review completed; estimate unavailable.",
            "manual_cost_override": True,
        },
    )
    assert response.status_code == 200, response.text

    # Security review
    response = client.post(
        f"/approvals/{approval_id}/stages/security/decision",
        headers=headers,
        json={
            "decision": "approved",
            "comments": "Security analysis reviewed.",
        },
    )
    assert response.status_code == 200, response.text


def test_client_submitted_plan_is_rejected(
    client, setup_data, valid_plan
):
    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )

    # "plan" is not an accepted field in ApprovalCreate.
    assert response.status_code == 422, response.text

    db = TestingSessionLocal()
    try:
        approval = db.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.infrastructure_request_id
                == setup_data["infra_request_id"]
            )
        )
        assert approval is None

        audit = db.scalar(select(ApprovalAuditEvent))
        assert audit is None
    finally:
        db.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("errored", True),
        ("complete", False),
        ("applyable", False),
    ],
)
def test_client_submitted_plan_is_rejected_regardless_of_flags(
    client, setup_data, valid_plan, field, value
):
    plan = dict(valid_plan)
    plan[field] = value

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": plan,
        },
    )

    # The API rejects client-supplied plan JSON at schema validation.
    assert response.status_code == 422, response.text


def test_duplicate_approval_returns_conflict(
    client, setup_data, valid_plan
):
    create_pending_approval(setup_data, valid_plan)

    # The duplicate check should run before the plan record lookup.
    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": 1,
        },
    )

    assert response.status_code == 409, response.text


def test_other_user_cannot_create_approval_for_request(
    client, setup_data, valid_plan
):
    plan_record_id = create_trusted_plan_record(
        setup_data,
        valid_plan,
    )

    db = TestingSessionLocal()
    try:
        other_user = User(
            email="other@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)

        headers = {
            "Authorization": (
                f"Bearer {create_access_token(str(other_user.id))}"
            )
        }
    finally:
        db.close()

    response = client.post(
        "/approvals",
        headers=headers,
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 404, response.text


def test_approval_requires_successful_trusted_plan(
    client, setup_data, valid_plan
):
    plan_record_id = create_trusted_plan_record(
        setup_data,
        valid_plan,
    )

    db = TestingSessionLocal()
    try:
        plan_record = db.get(TerraformPlanRecord, plan_record_id)
        assert plan_record is not None
        plan_record.status = "failed"
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text


def test_approval_rejects_stale_plan_specification(
    client, setup_data, valid_plan
):
    plan_record_id = create_trusted_plan_record(
        setup_data,
        valid_plan,
    )

    db = TestingSessionLocal()
    try:
        infra_request = db.get(
            InfrastructureRequest,
            setup_data["infra_request_id"],
        )
        assert infra_request is not None

        changed_requirements = dict(infra_request.requirements)
        changed_requirements["memory_gb"] = 8
        infra_request.requirements = changed_requirements
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text
    assert "changed after plan generation" in response.json()["detail"]


def test_approval_uses_trusted_plan_record(
    client, setup_data, valid_plan
):
    plan_record_id = create_trusted_plan_record(
        setup_data,
        valid_plan,
    )

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 201, response.text

    result = response.json()
    assert result["terraform_plan_record_id"] == plan_record_id
    assert result["infrastructure_request_id"] == (
        setup_data["infra_request_id"]
    )
    assert result["requester_id"] == setup_data["requester_id"]
    assert result["status"] == "pending"
    assert result["plan_summary"] == valid_plan



def test_approval_rejects_duplicate_for_same_plan(
    client, setup_data, valid_plan
):
    plan_record_id = create_trusted_plan_record(setup_data, valid_plan)

    payload = {
        "infrastructure_request_id": setup_data["infra_request_id"],
        "terraform_plan_record_id": plan_record_id,
    }

    first_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json=payload,
    )
    assert first_response.status_code == 201, first_response.text

    second_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json=payload,
    )

    assert second_response.status_code == 409
    assert "already exists" in second_response.json()["detail"]


def test_approval_allows_separate_plans_for_same_request(
    client, setup_data, valid_plan
):
    first_plan_id = create_trusted_plan_record(setup_data, valid_plan)
    second_plan_id = create_trusted_plan_record(setup_data, valid_plan)

    response_ids = []

    for plan_id in (first_plan_id, second_plan_id):
        response = client.post(
            "/approvals",
            headers=setup_data["requester_headers"],
            json={
                "infrastructure_request_id": setup_data["infra_request_id"],
                "terraform_plan_record_id": plan_id,
            },
        )

        assert response.status_code == 201, response.text
        response_ids.append(response.json()["id"])

    assert response_ids[0] != response_ids[1]

@pytest.mark.parametrize(
    "artifact_change,expected_detail",
    [
        ("missing_path", "artifact is unavailable"),
        ("missing_checksum", "artifact is unavailable"),
        ("fingerprint_mismatch", "does not match its artifact"),
    ],
)

def test_approval_rejects_invalid_artifact_metadata(
    client,
    setup_data,
    valid_plan,
    artifact_change,
    expected_detail,
):
    plan_record_id = create_trusted_plan_record(setup_data, valid_plan)

    db = TestingSessionLocal()
    try:
        plan_record = db.get(TerraformPlanRecord, plan_record_id)
        assert plan_record is not None

        if artifact_change == "missing_path":
            plan_record.artifact_path = None
        elif artifact_change == "missing_checksum":
            plan_record.artifact_sha256 = None
        else:
            plan_record.artifact_sha256 = "a" * 64

        db.commit()
    finally:
        db.close()

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text
    assert expected_detail in response.json()["detail"]


def test_approval_rejects_modified_plan_artifact(
    client,
    setup_data,
    valid_plan,
):
    plan_record_id = create_trusted_plan_record(setup_data, valid_plan)

    db = TestingSessionLocal()
    try:
        plan_record = db.get(TerraformPlanRecord, plan_record_id)
        assert plan_record is not None
        artifact_path = plan_record.artifact_path
    finally:
        db.close()

    with open(artifact_path, "wb") as artifact_file:
        artifact_file.write(b"modified-test-plan-artifact")

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text
    assert "integrity verification failed" in response.json()["detail"]

def test_non_admin_cannot_make_approval_decision(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["requester_headers"],
        json={"decision": "approved", "comments": "Test"},
    )

    assert response.status_code == 403, response.text


def test_admin_can_approve_and_audit_is_recorded(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)
    approve_all_stages(client, setup_data, approval_id)

    decision_response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={
            "decision": "approved",
            "comments": "Reviewed successfully",
        },
    )

    assert decision_response.status_code == 200, decision_response.text

    result = decision_response.json()
    assert result["status"] == "approved"
    assert result["reviewer_id"] == setup_data["reviewer_id"]
    assert result["reviewer_comments"] == "Reviewed successfully"
    assert result["decided_at"] is not None

    audit_response = client.get(
        f"/approvals/{approval_id}/audit",
        headers=setup_data["reviewer_headers"],
    )

    assert audit_response.status_code == 200, audit_response.text

    events = audit_response.json()
    assert [event["event_type"] for event in events] == [
        "submitted",
        "technical_approved",
        "cost_approved",
        "security_approved",
        "approved",
    ]
    assert events[-1]["actor_id"] == setup_data["reviewer_id"]
    assert events[-1]["previous_status"] == "pending"
    assert events[-1]["new_status"] == "approved"
    assert events[-1]["comments"] == "Reviewed successfully"


def test_admin_can_reject_approval(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={
            "decision": "rejected",
            "comments": "Needs security review",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert response.json()["reviewer_comments"] == "Needs security review"

    audit_response = client.get(
        f"/approvals/{approval_id}/audit",
        headers=setup_data["reviewer_headers"],
    )

    assert audit_response.status_code == 200, audit_response.text
    assert audit_response.json()[-1]["event_type"] == "rejected"


def test_cannot_decide_approval_twice(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)
    approve_all_stages(client, setup_data, approval_id)

    first = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={"decision": "approved"},
    )
    second = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={"decision": "rejected"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == (
        "This approval request has already been decided."
    )


def test_requester_cannot_approve_own_request(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)

    # The requester is not an admin, so the role check should deny this.
    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["requester_headers"],
        json={"decision": "approved"},
    )

    assert response.status_code == 403, response.text


def test_admin_cannot_approve_own_request(
    client, setup_data, valid_plan
):
    # Promote the requester to admin for this specific test.
    db = TestingSessionLocal()
    try:
        requester = db.get(User, setup_data["requester_id"])
        assert requester is not None
        requester.role = "admin"
        db.commit()
    finally:
        db.close()

    approval_id = create_pending_approval(setup_data, valid_plan)

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["requester_headers"],
        json={
            "decision": "approved",
            "comments": "Self-approval test",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == (
        "You cannot approve or reject your own request."
    )


def test_other_user_cannot_read_approval_or_audit(
    client, setup_data, valid_plan
):
    approval_id = create_pending_approval(setup_data, valid_plan)

    db = TestingSessionLocal()
    try:
        other_user = User(
            email="unauthorized-reader@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        db.add(other_user)
        db.commit()
        db.refresh(other_user)

        other_headers = {
            "Authorization": (
                f"Bearer {create_access_token(str(other_user.id))}"
            )
        }
    finally:
        db.close()

    approval_response = client.get(
        f"/approvals/{approval_id}",
        headers=other_headers,
    )
    audit_response = client.get(
        f"/approvals/{approval_id}/audit",
        headers=other_headers,
    )

    assert approval_response.status_code == 404
    assert audit_response.status_code == 404

@pytest.mark.parametrize(
    "security_changes",
    [
        {"passed": False},
        {"checks_completed": False},
        {"deployment_enabled": True},
        {"finding_count": 1},
        {"findings": [{"rule_id": "TEST_FINDING"}]},
        {
            "severity_counts": {
                "critical": 0,
                "high": 1,
                "medium": 0,
                "low": 0,
            }
        },
    ],
)
def test_approval_rejects_security_failed_plan(
    client, setup_data, valid_plan, security_changes
):
    plan = dict(valid_plan)
    plan["security_analysis"] = {
        **valid_plan["security_analysis"],
        **security_changes,
    }

    plan_record_id = create_trusted_plan_record(
        setup_data,
        plan,
    )

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text
    assert "security checks" in response.json()["detail"]


def test_approval_rejects_missing_security_analysis(
    client, setup_data, valid_plan
):
    plan = dict(valid_plan)
    plan.pop("security_analysis")

    plan_record_id = create_trusted_plan_record(
        setup_data,
        plan,
    )

    response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "terraform_plan_record_id": plan_record_id,
        },
    )

    assert response.status_code == 409, response.text

def create_fully_approved_lifecycle_request(
    client,
    setup_data,
    valid_plan,
):
    """Create a fully approved request with a verified test artifact."""

    approval_id = create_pending_approval(
        setup_data,
        valid_plan,
    )

    db = TestingSessionLocal()
    try:
        plan_record = db.scalar(
            select(TerraformPlanRecord).where(
                TerraformPlanRecord.infrastructure_request_id
                == setup_data["infra_request_id"]
            )
        )
        assert plan_record is not None
        artifact_path = plan_record.artifact_path
        assert artifact_path
    finally:
        db.close()

    approve_all_stages(client, setup_data, approval_id)

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={
            "decision": "approved",
            "comments": "All lifecycle test reviews completed.",
        },
    )
    assert response.status_code == 200, response.text

    return approval_id, artifact_path


def test_provision_rejects_approval_without_all_reviews(
    client,
    setup_data,
    valid_plan,
):
    approval_id = create_pending_approval(
        setup_data,
        valid_plan,
    )

    response = client.post(
        "/infrastructure-operations/provision",
        headers=setup_data["requester_headers"],
        json={"approval_request_id": approval_id},
    )

    assert response.status_code == 409
    assert "finally approved" in response.json()["detail"]


def test_fully_approved_request_queues_provision_operation(
    client,
    setup_data,
    valid_plan,
    monkeypatch,
):
    from types import SimpleNamespace

    from backend.app.workers.tasks import (
        execute_provision_operation,
    )
    from backend.app.services.terraform.artifacts import (
        delete_plan_artifact,
    )

    approval_id, artifact_path = (
        create_fully_approved_lifecycle_request(
            client,
            setup_data,
            valid_plan,
        )
    )

    monkeypatch.setattr(
        execute_provision_operation,
        "delay",
        lambda operation_id: SimpleNamespace(
            id="test-celery-task-id"
        ),
    )

    try:
        response = client.post(
            "/infrastructure-operations/provision",
            headers=setup_data["requester_headers"],
            json={
                "approval_request_id": approval_id,
                "comments": "Lifecycle API test",
            },
        )

        assert response.status_code == 202, response.text

        result = response.json()

        assert result["operation"] == "provision"
        assert result["status"] == "queued"
        assert result["celery_task_id"] == "test-celery-task-id"
        assert result["result_summary"]["terraform_apply_enabled"] is False

        operation_id = result["id"]

        detail = client.get(
            f"/infrastructure-operations/{operation_id}",
            headers=setup_data["requester_headers"],
        )

        assert detail.status_code == 200

        audit = client.get(
            f"/infrastructure-operations/{operation_id}/audit",
            headers=setup_data["requester_headers"],
        )

        assert audit.status_code == 200
        assert any(
            event["event_type"] == "provision_requested"
            for event in audit.json()
        )

    finally:
        delete_plan_artifact(artifact_path)



def test_provision_rejects_modified_plan_artifact(
    client,
    setup_data,
    valid_plan,
    monkeypatch,
):
    approval_id, artifact_path = create_fully_approved_lifecycle_request(
        client,
        setup_data,
        valid_plan,
    )

    with open(artifact_path, "wb") as artifact_file:
        artifact_file.write(b"modified-after-approval")

    response = client.post(
        "/infrastructure-operations/provision",
        headers=setup_data["requester_headers"],
        json={"approval_request_id": approval_id},
    )

    assert response.status_code == 409, response.text
    assert "integrity verification failed" in response.json()["detail"]

def test_duplicate_provision_operation_is_rejected(
    client,
    setup_data,
    valid_plan,
    monkeypatch,
):
    from types import SimpleNamespace

    from backend.app.workers.tasks import (
        execute_provision_operation,
    )
    from backend.app.services.terraform.artifacts import (
        delete_plan_artifact,
    )

    approval_id, artifact_path = (
        create_fully_approved_lifecycle_request(
            client,
            setup_data,
            valid_plan,
        )
    )

    monkeypatch.setattr(
        execute_provision_operation,
        "delay",
        lambda operation_id: SimpleNamespace(
            id="test-celery-task-id"
        ),
    )

    try:
        first_response = client.post(
            "/infrastructure-operations/provision",
            headers=setup_data["requester_headers"],
            json={"approval_request_id": approval_id},
        )

        assert first_response.status_code == 202, first_response.text

        second_response = client.post(
            "/infrastructure-operations/provision",
            headers=setup_data["requester_headers"],
            json={"approval_request_id": approval_id},
        )

        assert second_response.status_code == 409
        assert "already exists" in second_response.json()["detail"]

    finally:
        delete_plan_artifact(artifact_path)