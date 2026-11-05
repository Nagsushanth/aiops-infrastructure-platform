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

from backend.app.services.terraform.state_manager import create_state_record
from backend.app.services.terraform.state_version_manager import (
    TerraformStateVersionError,
    record_state_version,
    get_state_versions,
    verify_state_content,
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
            session_id="terraform-state-version-test",
        )
        session.add(conversation)
        session.flush()

        request = InfrastructureRequest(
            conversation_id=conversation.id,
            status="validated",
            requirements={},
        )
        session.add(request)
        session.commit()

        create_state_record(
            session,
            request.id,
            "aiops-test-state-bucket",
            f"requests/{request.id}/terraform.tfstate",
            "ap-south-1",
        )

        yield session, request.id

    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def create_version(session, request_id, version_id="version-001"):
    content = b"terraform-state-content"

    return record_state_version(
        session,
        request_id,
        version_id=version_id,
        state_serial=1,
        state_lineage="12345678-1234-1234-1234-123456789012",
        state_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def test_record_state_version(db):
    session, request_id = db

    version = create_version(session, request_id)

    assert version.id is not None
    assert version.version_id == "version-001"
    assert version.state_serial == 1
    assert version.size_bytes == len(b"terraform-state-content")


def test_duplicate_state_version_rejected(db):
    session, request_id = db

    create_version(session, request_id)

    with pytest.raises(TerraformStateVersionError) as exc:
        create_version(session, request_id)

    assert exc.value.args[0] == "STATE_VERSION_ALREADY_EXISTS"


def test_get_state_versions(db):
    session, request_id = db

    create_version(session, request_id, "version-001")
    create_version(session, request_id, "version-002")

    versions = get_state_versions(session, request_id)

    assert len(versions) == 2
    assert {item.version_id for item in versions} == {
        "version-001",
        "version-002",
    }


def test_missing_state_record_rejected(db):
    session, _ = db

    with pytest.raises(TerraformStateVersionError) as exc:
        get_state_versions(session, 999999)

    assert exc.value.args[0] == "STATE_RECORD_NOT_FOUND"


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("version_id", "", "STATE_VERSION_ID_INVALID"),
        ("state_serial", -1, "STATE_SERIAL_INVALID"),
        ("state_serial", True, "STATE_SERIAL_INVALID"),
        ("state_lineage", "invalid-uuid", "STATE_LINEAGE_INVALID"),
        ("state_sha256", "invalid", "STATE_CHECKSUM_INVALID"),
        ("size_bytes", 0, "STATE_SIZE_INVALID"),
        ("size_bytes", True, "STATE_SIZE_INVALID"),
    ],
)
def test_invalid_state_version_metadata(db, field, value, expected_error):
    session, request_id = db

    content = b"terraform-state-content"

    metadata = {
        "version_id": "version-invalid",
        "state_serial": 1,
        "state_lineage": "12345678-1234-1234-1234-123456789012",
        "state_sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }

    metadata[field] = value

    with pytest.raises(TerraformStateVersionError) as exc:
        record_state_version(session, request_id, **metadata)

    assert exc.value.args[0] == expected_error


def test_invalid_request_id_rejected(db):
    session, _ = db

    with pytest.raises(TerraformStateVersionError) as exc:
        record_state_version(
            session,
            True,
            version_id="version-001",
            state_serial=1,
            state_lineage="12345678-1234-1234-1234-123456789012",
            state_sha256=hashlib.sha256(b"state").hexdigest(),
            size_bytes=5,
        )

    assert exc.value.args[0] == "STATE_REQUEST_ID_INVALID"


def test_verify_state_content_success():
    content = b"terraform-state-content"
    checksum = hashlib.sha256(content).hexdigest()

    assert verify_state_content(content, checksum) is True


def test_verify_state_content_mismatch():
    content = b"terraform-state-content"
    checksum = hashlib.sha256(b"different-content").hexdigest()

    assert verify_state_content(content, checksum) is False


def test_verify_empty_state_content_rejected():
    with pytest.raises(TerraformStateVersionError) as exc:
        verify_state_content(b"", hashlib.sha256(b"").hexdigest())

    assert exc.value.args[0] == "STATE_CONTENT_INVALID"
