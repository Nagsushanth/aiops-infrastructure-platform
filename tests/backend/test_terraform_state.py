
import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models.base import Base
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.models.user import User

from backend.app.services.terraform.state_manager import (
    TerraformStateError,
    calculate_state_sha256,
    create_state_record,
    get_state_record,
    update_state_metadata,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = TestingSessionLocal()

    try:
        conversation = Conversation(
            user_id=None,
            session_id="terraform-state-test-session",
        )
        session.add(conversation)
        session.flush()

        infrastructure_request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="validated",
            requirements={},
        )
        session.add(infrastructure_request)
        session.commit()

        yield session, infrastructure_request.id

    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_create_state_record(db):
    session, request_id = db

    record = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    assert record.id is not None
    assert record.infrastructure_request_id == request_id
    assert record.backend_type == "s3"
    assert record.status == "pending"


def test_duplicate_state_record_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        create_state_record(
            session,
            request_id,
            "aiops-test-state-bucket",
            f"requests/{request_id}/terraform.tfstate",
            "ap-south-1",
        )

    assert exc.value.args[0] == "STATE_RECORD_ALREADY_EXISTS"


def test_nonexistent_request_is_rejected(db):
    session, _ = db

    with pytest.raises(TerraformStateError) as exc:
        create_state_record(
            session,
            999999,
            "aiops-test-state-bucket",
            "requests/999999/terraform.tfstate",
            "ap-south-1",
        )

    assert exc.value.args[0] == "INFRASTRUCTURE_REQUEST_NOT_FOUND"


def test_get_state_record(db):
    session, request_id = db

    created = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    retrieved = get_state_record(session, request_id)

    assert retrieved is not None
    assert retrieved.id == created.id


def test_update_state_metadata(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    checksum = hashlib.sha256(b"terraform-state").hexdigest()

    update_state_metadata(
        session,
        request_id,
        status="initializing",
    )

    updated = update_state_metadata(
        session,
        request_id,
        state_version_id="version-001",
        state_serial=1,
        state_lineage="12345678-1234-1234-1234-123456789012",
        state_sha256=checksum,
        status="active",
    )

    assert updated.state_version_id == "version-001"
    assert updated.state_serial == 1
    assert updated.state_lineage == "12345678-1234-1234-1234-123456789012"
    assert updated.state_sha256 == checksum
    assert updated.status == "active"


def test_negative_state_serial_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            state_serial=-1,
        )

    assert exc.value.args[0] == "STATE_SERIAL_INVALID"


def test_invalid_state_lineage_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            state_lineage="invalid-lineage",
        )

    assert exc.value.args[0] == "STATE_LINEAGE_INVALID"


def test_invalid_state_checksum_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            state_sha256="invalid-checksum",
        )

    assert exc.value.args[0] == "STATE_CHECKSUM_INVALID"


def test_invalid_state_status_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            status="unknown",
        )

    assert exc.value.args[0] == "STATE_STATUS_INVALID"


def test_calculate_state_sha256():
    content = b"terraform-state-content"

    checksum = calculate_state_sha256(content)

    assert checksum == hashlib.sha256(content).hexdigest()


def test_empty_state_content_is_rejected():
    with pytest.raises(TerraformStateError) as exc:
        calculate_state_sha256(b"")

    assert exc.value.args[0] == "STATE_CONTENT_INVALID"

def test_pending_to_active_transition_is_rejected(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            status="active",
        )

    assert exc.value.args[0] == "STATE_TRANSITION_INVALID"


def test_failed_state_can_retry_initialization(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    update_state_metadata(session, request_id, status="failed")

    updated = update_state_metadata(
        session,
        request_id,
        status="initializing",
    )

    assert updated.status == "initializing"


def test_archived_state_cannot_be_reinitialized(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    update_state_metadata(session, request_id, status="initializing")
    update_state_metadata(session, request_id, status="active")
    update_state_metadata(session, request_id, status="archived")

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            status="initializing",
        )

    assert exc.value.args[0] == "STATE_TRANSITION_INVALID"
