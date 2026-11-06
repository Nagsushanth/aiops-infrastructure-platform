from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import hashlib
import json
import uuid

import pytest
from botocore.exceptions import ClientError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.models.base import Base
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.services.terraform.state_manager import create_state_record
from backend.app.models.user import User

from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    create_s3_client,
    list_state_object_versions,
    parse_terraform_state,
    read_state_object,
    sync_s3_state_versions,
)

from backend.app.services.terraform.state_version_manager import (
    get_state_versions,
)


@pytest.fixture
def s3_client():
    return MagicMock()

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
            session_id="s3-state-storage-test",
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
            "test-state-bucket",
            f"requests/{request.id}/terraform.tfstate",
            "ap-south-1",
        )

        yield session, request.id

    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_read_latest_state_object(s3_client):
    content = b'{"version": 4, "serial": 1}'

    s3_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=content)),
        "VersionId": "version-001",
        "ETag": '"test-etag"',
        "LastModified": datetime.now(timezone.utc),
    }

    result = read_state_object(
        s3_client,
        "test-state-bucket",
        "requests/1/terraform.tfstate",
    )

    assert result["content"] == content
    assert result["version_id"] == "version-001"
    assert result["sha256"] is not None
    assert result["content_length"] == len(content)

    s3_client.get_object.assert_called_once_with(
        Bucket="test-state-bucket",
        Key="requests/1/terraform.tfstate",
    )


def test_read_specific_state_version(s3_client):
    content = b'{"version": 4, "serial": 2}'

    s3_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=content)),
        "VersionId": "version-002",
    }

    result = read_state_object(
        s3_client,
        "test-state-bucket",
        "requests/1/terraform.tfstate",
        version_id="version-002",
    )

    assert result["version_id"] == "version-002"

    s3_client.get_object.assert_called_once_with(
        Bucket="test-state-bucket",
        Key="requests/1/terraform.tfstate",
        VersionId="version-002",
    )


@pytest.mark.parametrize(
    ("bucket", "key", "expected_error"),
    [
        ("", "requests/1/terraform.tfstate", "S3_BUCKET_INVALID"),
        ("test-state-bucket", "", "S3_OBJECT_KEY_INVALID"),
    ],
)
def test_invalid_s3_location_is_rejected(
    s3_client,
    bucket,
    key,
    expected_error,
):
    with pytest.raises(S3StateStorageError) as exc:
        read_state_object(s3_client, bucket, key)

    assert exc.value.args[0] == expected_error
    s3_client.get_object.assert_not_called()


def test_empty_state_content_is_rejected(s3_client):
    s3_client.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=b"")),
    }

    with pytest.raises(S3StateStorageError) as exc:
        read_state_object(
            s3_client,
            "test-state-bucket",
            "requests/1/terraform.tfstate",
        )

    assert exc.value.args[0] == "S3_STATE_CONTENT_EMPTY"


def test_s3_read_failure_is_handled(s3_client):
    s3_client.get_object.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "Access denied",
            }
        },
        "GetObject",
    )

    with pytest.raises(S3StateStorageError) as exc:
        read_state_object(
            s3_client,
            "test-state-bucket",
            "requests/1/terraform.tfstate",
        )

    assert exc.value.args[0] == "S3_STATE_READ_FAILED"

def test_list_state_object_versions(s3_client):
    paginator = MagicMock()

    paginator.paginate.return_value = [
        {
            "Versions": [
                {
                    "Key": "requests/1/terraform.tfstate",
                    "VersionId": "version-002",
                    "IsLatest": True,
                    "Size": 250,
                    "LastModified": datetime.now(timezone.utc),
                },
                {
                    "Key": "requests/1/terraform.tfstate",
                    "VersionId": "version-001",
                    "IsLatest": False,
                    "Size": 200,
                    "LastModified": datetime.now(timezone.utc),
                },
                {
                    "Key": "requests/10/terraform.tfstate",
                    "VersionId": "unrelated-version",
                    "IsLatest": True,
                    "Size": 100,
                    "LastModified": datetime.now(timezone.utc),
                },
            ],
        },
    ]

    s3_client.get_paginator.return_value = paginator

    result = list_state_object_versions(
        s3_client,
        "test-state-bucket",
        "requests/1/terraform.tfstate",
    )

    assert len(result) == 2
    assert result[0]["version_id"] == "version-002"
    assert result[0]["is_latest"] is True
    assert result[1]["version_id"] == "version-001"
    assert result[1]["is_latest"] is False

    s3_client.get_paginator.assert_called_once_with(
        "list_object_versions"
    )

    paginator.paginate.assert_called_once_with(
        Bucket="test-state-bucket",
        Prefix="requests/1/terraform.tfstate",
    )

def test_parse_valid_terraform_state():
    lineage = str(uuid.uuid4())

    content = json.dumps({
        "version": 4,
        "terraform_version": "1.15.6",
        "serial": 3,
        "lineage": lineage,
        "outputs": {},
        "resources": [],
    }).encode()

    result = parse_terraform_state(content)

    assert result["serial"] == 3
    assert result["lineage"] == lineage


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"invalid-json",
        b"[]",
        b'{"version": 3, "serial": 1, "lineage": "12345678-1234-1234-1234-123456789012"}',
        b'{"version": 4, "serial": -1, "lineage": "12345678-1234-1234-1234-123456789012"}',
        b'{"version": 4, "serial": 1, "lineage": "invalid"}',
    ],
)
def test_invalid_terraform_state_is_rejected(content):
    with pytest.raises(S3StateStorageError):
        parse_terraform_state(content)

def test_sync_s3_state_versions(db, s3_client):
    from unittest.mock import patch

    session, request_id = db

    state_content = json.dumps({
        "version": 4,
        "terraform_version": "1.15.6",
        "serial": 2,
        "lineage": str(uuid.uuid4()),
        "outputs": {},
        "resources": [],
    }).encode()

    versions = [
        {
            "version_id": "version-001",
            "size_bytes": len(state_content),
        },
    ]

    state = {
        "content": state_content,
        "content_length": len(state_content),
        "sha256": hashlib.sha256(state_content).hexdigest(),
    }

    with (
        patch(
            "backend.app.services.terraform.s3_state_storage.list_state_object_versions",
            return_value=versions,
        ),
        patch(
            "backend.app.services.terraform.s3_state_storage.read_state_object",
            return_value=state,
        ),
    ):
        result = sync_s3_state_versions(
            session,
            s3_client,
            request_id,
            "test-state-bucket",
            f"requests/{request_id}/terraform.tfstate",
        )

    assert result == {
        "total_versions": 1,
        "recorded": 1,
        "skipped": 0,
    }

    assert len(
        get_state_versions(session, request_id)
    ) == 1

def test_sync_s3_state_versions_skips_existing_version(db, s3_client):
    session, request_id = db

    state_content = json.dumps({
        "version": 4,
        "terraform_version": "1.10.0",
        "serial": 1,
        "lineage": str(uuid.uuid4()),
        "outputs": {},
        "resources": [],
    }).encode()

    versions = [
        {
            "version_id": "version-1",
            "is_latest": True,
            "size_bytes": len(state_content),
            "last_modified": datetime.now(timezone.utc),
        }
    ]

    state = {
        "content": state_content,
        "version_id": "version-1",
        "content_length": len(state_content),
        "sha256": hashlib.sha256(state_content).hexdigest(),
    }

    with (
        patch(
            "backend.app.services.terraform.s3_state_storage.list_state_object_versions",
            return_value=versions,
        ),
        patch(
            "backend.app.services.terraform.s3_state_storage.read_state_object",
            return_value=state,
        ),
    ):
        first_result = sync_s3_state_versions(
            session, s3_client, request_id,
            "test-state-bucket", f"requests/{request_id}/terraform.tfstate",
        )

        second_result = sync_s3_state_versions(
            session, s3_client, request_id,
            "test-state-bucket", f"requests/{request_id}/terraform.tfstate",
        )

    assert first_result["recorded"] == 1
    assert second_result["recorded"] == 0
    assert second_result["skipped"] == 1
    assert len(get_state_versions(session, request_id)) == 1

def test_sync_s3_state_versions_rejects_size_mismatch(db, s3_client):
    session, request_id = db

    state_content = b'{"version":4,"serial":1}'

    versions = [
        {
            "version_id": "version-mismatch",
            "is_latest": True,
            "size_bytes": len(state_content) + 10,
            "last_modified": datetime.now(timezone.utc),
        }
    ]

    state = {
        "content": state_content,
        "version_id": "version-mismatch",
        "content_length": len(state_content),
        "sha256": hashlib.sha256(state_content).hexdigest(),
    }

    with (
        patch(
            "backend.app.services.terraform.s3_state_storage.list_state_object_versions",
            return_value=versions,
        ),
        patch(
            "backend.app.services.terraform.s3_state_storage.read_state_object",
            return_value=state,
        ),
    ):
        with pytest.raises(
            S3StateStorageError,
            match="S3_STATE_SIZE_MISMATCH",
        ):
            sync_s3_state_versions(
                session,
                s3_client,
                request_id,
                "test-state-bucket",
                f"requests/{request_id}/terraform.tfstate",
            )

    assert get_state_versions(session, request_id) == []

def test_sync_s3_state_versions_rejects_invalid_state(db, s3_client):
    session, request_id = db

    state_content = b'{"version":4,"serial":-1}'

    versions = [
        {
            "version_id": "invalid-version",
            "is_latest": True,
            "size_bytes": len(state_content),
            "last_modified": datetime.now(timezone.utc),
        }
    ]

    state = {
        "content": state_content,
        "version_id": "invalid-version",
        "content_length": len(state_content),
        "sha256": hashlib.sha256(state_content).hexdigest(),
    }

    with (
        patch(
            "backend.app.services.terraform.s3_state_storage.list_state_object_versions",
            return_value=versions,
        ),
        patch(
            "backend.app.services.terraform.s3_state_storage.read_state_object",
            return_value=state,
        ),
    ):
        with pytest.raises(
            S3StateStorageError,
            match="S3_STATE_JSON_INVALID",
        ):
            sync_s3_state_versions(
                session,
                s3_client,
                request_id,
                "test-state-bucket",
                f"requests/{request_id}/terraform.tfstate",
            )

    assert get_state_versions(session, request_id) == []

def test_sync_s3_state_versions_rolls_back_on_invalid_second_version(
    db, s3_client
):
    session, request_id = db

    valid_state = json.dumps({
        "version": 4,
        "serial": 1,
        "lineage": str(uuid.uuid4()),
    }).encode()

    invalid_state = b'{"version":4,"serial":-1}'

    versions = [
        {
            "version_id": "valid-version",
            "is_latest": False,
            "size_bytes": len(valid_state),
            "last_modified": datetime.now(timezone.utc),
        },
        {
            "version_id": "invalid-version",
            "is_latest": True,
            "size_bytes": len(invalid_state),
            "last_modified": datetime.now(timezone.utc),
        },
    ]

    states = {
        "valid-version": {
            "content": valid_state,
            "content_length": len(valid_state),
            "sha256": hashlib.sha256(valid_state).hexdigest(),
        },
        "invalid-version": {
            "content": invalid_state,
            "content_length": len(invalid_state),
            "sha256": hashlib.sha256(invalid_state).hexdigest(),
        },
    }

    def mock_read_state(client, bucket, key, version_id=None):
        return states[version_id]

    with (
        patch(
            "backend.app.services.terraform.s3_state_storage.list_state_object_versions",
            return_value=versions,
        ),
        patch(
            "backend.app.services.terraform.s3_state_storage.read_state_object",
            side_effect=mock_read_state,
        ),
    ):
        with pytest.raises(
            S3StateStorageError,
            match="S3_STATE_JSON_INVALID",
        ):
            sync_s3_state_versions(
                session,
                s3_client,
                request_id,
                "test-state-bucket",
                f"requests/{request_id}/terraform.tfstate",
            )

    assert get_state_versions(session, request_id) == []

def test_create_s3_client_uses_requested_region(monkeypatch):
    mock_session = MagicMock()
    mock_boto_session = MagicMock(return_value=mock_session)

    monkeypatch.setattr(
        "backend.app.services.terraform.s3_state_storage.boto3.Session",
        mock_boto_session,
    )

    monkeypatch.setenv("AWS_PROFILE", "aiops-training")

    client = create_s3_client(region_name="ap-south-1")

    mock_boto_session.assert_called_once_with(
        profile_name="aiops-training",
        region_name="ap-south-1",
    )

    mock_session.client.assert_called_once_with("s3")
    assert client == mock_session.client.return_value

def test_create_s3_client_uses_default_region(monkeypatch):
    mock_session = MagicMock()
    mock_boto_session = MagicMock(return_value=mock_session)

    monkeypatch.setattr(
        "backend.app.services.terraform.s3_state_storage.boto3.Session",
        mock_boto_session,
    )

    monkeypatch.delenv("TF_STATE_REGION", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "aiops-training")

    create_s3_client()

    mock_boto_session.assert_called_once_with(
        profile_name="aiops-training",
        region_name="ap-south-1",
    )

    mock_session.client.assert_called_once_with("s3")