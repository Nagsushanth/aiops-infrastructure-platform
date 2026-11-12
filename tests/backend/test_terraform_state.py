
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
import json
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
)
from backend.app.services.terraform.state_manager import (
    TerraformStateError,
    calculate_state_sha256,
    create_state_record,
    get_state_record,
    update_state_metadata,
    verify_active_state_snapshot,
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

@pytest.mark.parametrize("request_id", [True, False])
def test_create_state_record_rejects_boolean_request_id(db, request_id):
    session, _ = db

    with pytest.raises(TerraformStateError) as exc:
        create_state_record(
            session,
            request_id,
            "aiops-test-state-bucket",
            "requests/1/terraform.tfstate",
            "ap-south-1",
        )

    assert exc.value.args[0] == "STATE_REQUEST_ID_INVALID"


@pytest.mark.parametrize(
    "lineage",
    [
        "12345678-1234-1234-1234-12345678901Z",
        "12345678123412341234123456789012",
        "{12345678-1234-1234-1234-123456789012}",
        "",
    ],
)
def test_noncanonical_state_lineage_is_rejected(db, lineage):
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
            state_lineage=lineage,
        )

    assert exc.value.args[0] == "STATE_LINEAGE_INVALID"


def test_uppercase_valid_state_lineage_is_normalized(db):
    session, request_id = db

    create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    lineage = "12345678-1234-1234-1234-123456789ABC"

    updated = update_state_metadata(
        session,
        request_id,
        state_lineage=lineage,
    )

    assert updated.state_lineage == lineage.lower()


def test_invalid_persisted_state_status_is_handled(db):
    session, request_id = db

    record = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    record.status = "unexpected"
    session.commit()

    with pytest.raises(TerraformStateError) as exc:
        update_state_metadata(
            session,
            request_id,
            status="initializing",
        )

    assert exc.value.args[0] == "STATE_STATUS_INVALID"


def _create_active_state_snapshot(db):
    session, request_id = db

    record = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    content = json.dumps({
        "version": 4,
        "serial": 3,
        "lineage": "12345678-1234-1234-1234-123456789012",
        "resources": [],
    }).encode("utf-8")

    checksum = hashlib.sha256(content).hexdigest()

    update_state_metadata(
        session,
        request_id,
        status="initializing",
    )

    update_state_metadata(
        session,
        request_id,
        state_version_id="version-001",
        state_serial=3,
        state_lineage="12345678-1234-1234-1234-123456789012",
        state_sha256=checksum,
        status="active",
    )

    return record, content, checksum


def test_verify_active_state_snapshot_success(db, monkeypatch):
    record, content, checksum = _create_active_state_snapshot(db)

    monkeypatch.setattr(
        "backend.app.services.terraform.state_manager.read_state_object",
        lambda *args, **kwargs: {
            "content": content,
            "version_id": "version-001",
            "sha256": checksum,
        },
    )

    snapshot = verify_active_state_snapshot(
        object(),
        record,
    )

    assert snapshot == {
        "version_id": "version-001",
        "serial": 3,
        "lineage": "12345678-1234-1234-1234-123456789012",
        "sha256": checksum,
    }


def test_verify_inactive_state_snapshot_is_rejected(db):
    session, request_id = db

    record = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    with pytest.raises(TerraformStateError) as exc:
        verify_active_state_snapshot(object(), record)

    assert exc.value.args[0] == "STATE_NOT_ACTIVE"


def test_verify_incomplete_state_snapshot_is_rejected(db):
    session, request_id = db

    record = create_state_record(
        session,
        request_id,
        "aiops-test-state-bucket",
        f"requests/{request_id}/terraform.tfstate",
        "ap-south-1",
    )

    update_state_metadata(
        session,
        request_id,
        status="initializing",
    )

    update_state_metadata(
        session,
        request_id,
        status="active",
    )

    with pytest.raises(TerraformStateError) as exc:
        verify_active_state_snapshot(object(), record)

    assert exc.value.args[0] == "STATE_SNAPSHOT_INCOMPLETE"


def test_verify_s3_state_read_failure_is_rejected(db, monkeypatch):
    record, _, _ = _create_active_state_snapshot(db)

    def fail_read(*args, **kwargs):
        raise S3StateStorageError("S3_STATE_READ_FAILED")

    monkeypatch.setattr(
        "backend.app.services.terraform.state_manager.read_state_object",
        fail_read,
    )

    with pytest.raises(TerraformStateError) as exc:
        verify_active_state_snapshot(object(), record)

    assert exc.value.args[0] == "STATE_SNAPSHOT_READ_FAILED"


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("state_version_id", "version-002"),
        ("state_serial", 4),
        (
            "state_lineage",
            "87654321-4321-4321-4321-210987654321",
        ),
        ("state_sha256", "b" * 64),
    ],
)
def test_verify_changed_state_snapshot_is_rejected(
    db,
    monkeypatch,
    field,
    changed_value,
):
    session, _ = db
    record, content, checksum = _create_active_state_snapshot(db)

    setattr(record, field, changed_value)
    session.commit()

    monkeypatch.setattr(
        "backend.app.services.terraform.state_manager.read_state_object",
        lambda *args, **kwargs: {
            "content": content,
            "version_id": "version-001",
            "sha256": checksum,
        },
    )

    with pytest.raises(TerraformStateError) as exc:
        verify_active_state_snapshot(object(), record)

    assert exc.value.args[0] == "STATE_SNAPSHOT_CHANGED"
