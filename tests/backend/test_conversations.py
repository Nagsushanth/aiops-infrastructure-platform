
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.core.dependencies import get_db
from backend.app.core.security import create_access_token
from backend.app.models.base import Base
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.models.user import User


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
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
def users(client):
    db = TestingSessionLocal()
    try:
        user_a = User(
            email="user-a@example.com",
            password_hash="test-hash-a",
            role="user",
            is_active=True,
        )
        user_b = User(
            email="user-b@example.com",
            password_hash="test-hash-b",
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


def test_create_conversation(client, users):
    response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={
            "session_id": "test-session-001",
            "title": "Test infrastructure request",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["session_id"] == "test-session-001"
    assert data["title"] == "Test infrastructure request"
    assert data["id"]
    assert data["user_id"] == users["a"]["id"]


def test_duplicate_session_returns_conflict(client, users):
    payload = {
        "session_id": "duplicate-session",
        "title": "Test conversation",
    }
    headers = users["a"]["headers"]

    first = client.post("/conversations", headers=headers, json=payload)
    second = client.post("/conversations", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 409


def test_get_conversation_by_session(client, users):
    client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={"session_id": "lookup-session", "title": "Lookup test"},
    )

    response = client.get(
        "/conversations/by-session/lookup-session",
        headers=users["a"]["headers"],
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "lookup-session"


def test_missing_session_returns_404(client, users):
    response = client.get(
        "/conversations/by-session/not-found",
        headers=users["a"]["headers"],
    )

    assert response.status_code == 404


def test_create_and_retrieve_messages(client, users):
    conversation_response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={
            "session_id": "message-session",
            "title": "Message test",
        },
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    message_response = client.post(
        f"/conversations/{conversation_id}/messages",
        headers=users["a"]["headers"],
        json={
            "role": "user",
            "content": "I need a development EC2 instance.",
        },
    )

    assert message_response.status_code == 201
    assert message_response.json()["content"] == (
        "I need a development EC2 instance."
    )

    history_response = client.get(
        f"/conversations/{conversation_id}/messages",
        headers=users["a"]["headers"],
    )

    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == (
        "I need a development EC2 instance."
    )


def test_missing_conversation_messages_return_404(client, users):
    response = client.get(
        "/conversations/999999/messages",
        headers=users["a"]["headers"],
    )

    assert response.status_code == 404


def test_invalid_message_role_returns_422(client, users):
    conversation_response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={"session_id": "invalid-role-session"},
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages",
        headers=users["a"]["headers"],
        json={"role": "system", "content": "Invalid role"},
    )

    assert response.status_code == 422


def test_empty_message_content_returns_422(client, users):
    conversation_response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={"session_id": "empty-message-session"},
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    response = client.post(
        f"/conversations/{conversation_id}/messages",
        headers=users["a"]["headers"],
        json={"role": "user", "content": ""},
    )

    assert response.status_code == 422


def test_conversation_endpoints_require_authentication(client):
    assert client.post(
        "/conversations",
        json={"session_id": "unauthenticated-session"},
    ).status_code == 401

    assert client.get(
        "/conversations/by-session/anything"
    ).status_code == 401

    assert client.get(
        "/conversations/1/messages"
    ).status_code == 401


def test_user_cannot_access_another_users_conversation(client, users):
    create_response = client.post(
        "/conversations",
        headers=users["a"]["headers"],
        json={"session_id": "private-session", "title": "Private"},
    )
    assert create_response.status_code == 201
    conversation_id = create_response.json()["id"]

    get_response = client.get(
        f"/conversations/{conversation_id}/messages",
        headers=users["b"]["headers"],
    )
    add_response = client.post(
        f"/conversations/{conversation_id}/messages",
        headers=users["b"]["headers"],
        json={"role": "user", "content": "Unauthorized access"},
    )
    lookup_response = client.get(
        "/conversations/by-session/private-session",
        headers=users["b"]["headers"],
    )

    assert get_response.status_code == 404
    assert add_response.status_code == 404
    assert lookup_response.status_code == 404

    db = TestingSessionLocal()
    try:
        saved_messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .all()
        )
        assert saved_messages == []
    finally:
        db.close()
