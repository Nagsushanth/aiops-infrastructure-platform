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
from backend.app.models.approval import ApprovalAuditEvent, ApprovalRequest
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.user import User


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

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def setup_data(client):
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

        result = {
            "requester_id": requester.id,
            "reviewer_id": reviewer.id,
            "infra_request_id": infra_request.id,
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
        return result
    finally:
        db.close()


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
    }


def test_create_approval_calculates_fingerprint_and_audit(
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

    assert response.status_code == 201, response.text
    body = response.json()

    canonical = json.dumps(
        valid_plan,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected_fingerprint = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()

    assert body["status"] == "pending"
    assert len(expected_fingerprint) == 64

    db = TestingSessionLocal()
    try:
        approval = db.get(ApprovalRequest, body["id"])
        assert approval is not None
        assert approval.plan_fingerprint == expected_fingerprint

        audit = db.scalar(
            select(ApprovalAuditEvent).where(
                ApprovalAuditEvent.approval_request_id == approval.id
            )
        )
        assert audit is not None
        assert audit.event_type == "submitted"
        assert audit.event_metadata["plan_fingerprint"] == expected_fingerprint
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
def test_rejects_plan_with_invalid_status(
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

    assert response.status_code == 422


def test_duplicate_approval_returns_conflict(
    client, setup_data, valid_plan
):
    payload = {
        "infrastructure_request_id": setup_data["infra_request_id"],
        "plan": valid_plan,
    }
    headers = setup_data["requester_headers"]

    first = client.post("/approvals", headers=headers, json=payload)
    second = client.post("/approvals", headers=headers, json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 409


def test_other_user_cannot_create_approval_for_request(
    client, setup_data, valid_plan
):
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
            "plan": valid_plan,
        },
    )

    assert response.status_code == 404


def test_non_admin_cannot_make_approval_decision(
    client, setup_data, valid_plan
):
    create_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )
    assert create_response.status_code == 201, create_response.text
    approval_id = create_response.json()["id"]

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["requester_headers"],
        json={"decision": "approved", "comments": "Test"},
    )

    assert response.status_code == 403



def test_admin_can_approve_and_audit_is_recorded(
    client, setup_data, valid_plan
):
    create_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )
    assert create_response.status_code == 201, create_response.text
    approval_id = create_response.json()["id"]

    decision_response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={"decision": "approved", "comments": "Reviewed successfully"},
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
        "approved",
    ]
    assert events[-1]["actor_id"] == setup_data["reviewer_id"]
    assert events[-1]["previous_status"] == "pending"
    assert events[-1]["new_status"] == "approved"
    assert events[-1]["comments"] == "Reviewed successfully"


def test_admin_can_reject_approval(
    client, setup_data, valid_plan
):
    create_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )
    assert create_response.status_code == 201, create_response.text
    approval_id = create_response.json()["id"]

    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["reviewer_headers"],
        json={"decision": "rejected", "comments": "Needs security review"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"
    assert response.json()["reviewer_comments"] == "Needs security review"

    audit_response = client.get(
        f"/approvals/{approval_id}/audit",
        headers=setup_data["reviewer_headers"],
    )
    assert audit_response.status_code == 200
    assert audit_response.json()[-1]["event_type"] == "rejected"


def test_cannot_decide_approval_twice(
    client, setup_data, valid_plan
):
    create_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )
    assert create_response.status_code == 201, create_response.text
    approval_id = create_response.json()["id"]

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
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "This approval request has already been decided."
    )


def test_requester_cannot_approve_own_request(
    client, setup_data, valid_plan
):
    create_response = client.post(
        "/approvals",
        headers=setup_data["requester_headers"],
        json={
            "infrastructure_request_id": setup_data["infra_request_id"],
            "plan": valid_plan,
        },
    )
    assert create_response.status_code == 201, create_response.text
    approval_id = create_response.json()["id"]

    # The requester is not an admin, so the role check should deny this.
    response = client.post(
        f"/approvals/{approval_id}/decision",
        headers=setup_data["requester_headers"],
        json={"decision": "approved"},
    )

    assert response.status_code == 403

