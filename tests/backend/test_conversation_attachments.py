
import io
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.core.dependencies import get_db
from backend.app.core.security import create_access_token
from backend.app.models.base import Base
from backend.app.models.user import User
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.models.message_attachment import MessageAttachment
from backend.app.services import attachments as attachment_service
from backend.app.api import conversations as conversation_api

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
def client(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=test_engine)

    monkeypatch.setattr(
        attachment_service,
        "UPLOAD_DIRECTORY",
        tmp_path,
    )
    monkeypatch.setattr(
        conversation_api,
        "UPLOAD_DIRECTORY",
        tmp_path,
    )

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
def users(client):
    db = TestingSessionLocal()

    try:
        user_a = User(
            email="attachment-a@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )
        user_b = User(
            email="attachment-b@example.com",
            password_hash="test-hash",
            role="user",
            is_active=True,
        )

        db.add_all([user_a, user_b])
        db.commit()
        db.refresh(user_a)
        db.refresh(user_b)

        return {
            "a": {
                "id": user_a.id,
                "headers": {
                    "Authorization": (
                        f"Bearer {create_access_token(str(user_a.id))}"
                    )
                },
            },
            "b": {
                "id": user_b.id,
                "headers": {
                    "Authorization": (
                        f"Bearer {create_access_token(str(user_b.id))}"
                    )
                },
            },
        }
    finally:
        db.close()


@pytest.fixture
def conversation(client, users):
    response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={
            "session_id": "attachment-test-session",
            "title": "Attachment testing",
        },
    )

    assert response.status_code == 201
    return response.json()["id"]


def test_upload_text_attachment(client, users, conversation):
    response = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": (
                "notes.txt",
                b"My AIOps project notes",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201

    data = response.json()
    assert data["attachment"]["original_filename"] == "notes.txt"
    assert data["attachment"]["content_type"] == "text/plain"
    assert data["attachment"]["size_bytes"] == len(
        b"My AIOps project notes"
    )
    assert data["message_content"] == "📎 notes.txt"

    db = TestingSessionLocal()
    try:
        message = db.get(Message, data["message_id"])
        attachment = db.get(
            MessageAttachment,
            data["attachment"]["id"],
        )

        assert message is not None
        assert message.role == "user"
        assert message.content == "📎 notes.txt"

        assert attachment is not None
        assert attachment.uploaded_by == users["a"]["id"]
        assert attachment.conversation_id == conversation

        assert (
            attachment_service.UPLOAD_DIRECTORY
            / attachment.storage_filename
        ).is_file()
    finally:
        db.close()


def test_list_conversation_attachments(
    client,
    users,
    conversation,
):
    upload = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("notes.txt", b"Attachment listing", "text/plain")
        },
    )

    assert upload.status_code == 201

    response = client.get(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["original_filename"] == "notes.txt"


def test_download_attachment(
    client,
    users,
    conversation,
):
    upload = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("download.txt", b"Download test content", "text/plain")
        },
    )

    assert upload.status_code == 201

    attachment_id = upload.json()["attachment"]["id"]

    response = client.get(
        f"/conversations/{conversation}/attachments/{attachment_id}",
        headers=users["a"]["headers"],
    )

    assert response.status_code == 200
    assert response.content == b"Download test content"
    assert "download.txt" in response.headers["content-disposition"]


def test_unsupported_file_type_is_rejected(
    client,
    users,
    conversation,
):
    response = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("malware.exe", b"not allowed", "application/octet-stream")
        },
    )

    assert response.status_code == 415

    db = TestingSessionLocal()
    try:
        assert db.scalars(
            select(MessageAttachment)
        ).all() == []
    finally:
        db.close()


def test_invalid_pdf_signature_is_rejected(
    client,
    users,
    conversation,
):
    response = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("fake.pdf", b"This is not a PDF", "application/pdf")
        },
    )

    assert response.status_code == 400


def test_file_larger_than_10mb_is_rejected(
    client,
    users,
    conversation,
):
    oversized_content = b"a" * (10 * 1024 * 1024 + 1)

    response = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("large.txt", oversized_content, "text/plain")
        },
    )

    assert response.status_code == 413


def test_other_user_cannot_list_attachments(
    client,
    users,
    conversation,
):
    response = client.get(
        f"/conversations/{conversation}/attachments",
        headers=users["b"]["headers"],
    )

    assert response.status_code == 404


def test_other_user_cannot_download_attachment(
    client,
    users,
    conversation,
):
    upload = client.post(
        f"/conversations/{conversation}/attachments",
        headers=users["a"]["headers"],
        files={
            "file": ("private.txt", b"Private content", "text/plain")
        },
    )

    assert upload.status_code == 201

    attachment_id = upload.json()["attachment"]["id"]

    response = client.get(
        f"/conversations/{conversation}/attachments/{attachment_id}",
        headers=users["b"]["headers"],
    )

    assert response.status_code == 404


def test_upload_requires_authentication(
    client,
    conversation,
):
    response = client.post(
        f"/conversations/{conversation}/attachments",
        files={
            "file": ("notes.txt", b"Unauthorized", "text/plain")
        },
    )

    assert response.status_code == 401
