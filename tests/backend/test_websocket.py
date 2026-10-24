
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from backend.app.main import app
from backend.app.core.security import create_access_token
from backend.app.models.base import Base
from backend.app.models.conversation import Conversation
from backend.app.models.user import User
import backend.app.api.websocket as websocket_module


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
def websocket_client(monkeypatch):
    Base.metadata.create_all(bind=test_engine)

    monkeypatch.setattr(
        websocket_module,
        "SessionLocal",
        TestingSessionLocal,
    )

    with TestClient(app) as client:
        yield client

    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def websocket_users(websocket_client):
    db = TestingSessionLocal()

    try:
        user_a = User(
            email="ws-user-a@example.com",
            password_hash="test-hash-a",
            role="user",
            is_active=True,
        )
        user_b = User(
            email="ws-user-b@example.com",
            password_hash="test-hash-b",
            role="user",
            is_active=True,
        )
        db.add_all([user_a, user_b])
        db.commit()
        db.refresh(user_a)
        db.refresh(user_b)

        conversation = Conversation(
            session_id="ws-private-session",
            title="WebSocket security test",
            user_id=user_a.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        return {
            "a": {
                "id": user_a.id,
                "token": create_access_token(str(user_a.id)),
            },
            "b": {
                "id": user_b.id,
                "token": create_access_token(str(user_b.id)),
            },
            "session_id": conversation.session_id,
            "conversation_id": conversation.id,
        }
    finally:
        db.close()


def test_websocket_rejects_missing_authentication(
    websocket_client,
    websocket_users,
):
    with websocket_client.websocket_connect(
        f"/ws/conversations/{websocket_users['session_id']}"
    ) as ws:
        ws.send_json({"type": "ping"})
        response = ws.receive_json()

        assert response["type"] == "error"
        assert "Authentication" in response["detail"]

        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()

        assert exc.value.code == 4401


def test_websocket_rejects_invalid_token(
    websocket_client,
    websocket_users,
):
    with websocket_client.websocket_connect(
        f"/ws/conversations/{websocket_users['session_id']}"
    ) as ws:
        ws.send_json({
            "type": "authenticate",
            "token": "invalid-token",
        })

        response = ws.receive_json()
        assert response["type"] == "error"

        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()

        assert exc.value.code == 4401


def test_websocket_rejects_another_users_conversation(
    websocket_client,
    websocket_users,
):
    with websocket_client.websocket_connect(
        f"/ws/conversations/{websocket_users['session_id']}"
    ) as ws:
        ws.send_json({
            "type": "authenticate",
            "token": websocket_users["b"]["token"],
        })

        response = ws.receive_json()
        assert response["type"] == "error"
        assert "Conversation not found" in response["detail"]

        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()

        assert exc.value.code == 4404


def test_websocket_accepts_owner_authentication(
    websocket_client,
    websocket_users,
):
    with websocket_client.websocket_connect(
        f"/ws/conversations/{websocket_users['session_id']}"
    ) as ws:
        ws.send_json({
            "type": "authenticate",
            "token": websocket_users["a"]["token"],
        })

        response = ws.receive_json()

        assert response["type"] == "connected"
        assert response["conversation_id"] == (
            websocket_users["conversation_id"]
        )

def test_websocket_collects_requirements_across_messages(
    websocket_client,
    websocket_users,
):
    with websocket_client.websocket_connect(
        f"/ws/conversations/{websocket_users['session_id']}"
    ) as ws:
        ws.send_json({
            "type": "authenticate",
            "token": websocket_users["a"]["token"],
        })

        connected = ws.receive_json()
        assert connected["type"] == "connected"

        def send_message_and_get_result(content):
            ws.send_json({
                "type": "message",
                "content": content,
            })

            saved = ws.receive_json()
            result_message = ws.receive_json()
            assistant_message = ws.receive_json()

            assert saved["type"] == "message_saved"
            assert result_message["type"] == "requirements_result"
            assert assistant_message["type"] == "assistant_message"

            return result_message["result"]

        # Start with an incomplete infrastructure request.
        result = send_message_and_get_result(
            "AWS EC2 development instance in region ap-south-1"
        )
        assert result["status"] == "needs_clarification"
        assert result["requirements"]["cloud_provider"] == "aws"
        assert result["requirements"]["resource_type"] == "EC2"
        assert result["requirements"]["environment"] == "development"
        assert result["requirements"]["region"] == "ap-south-1"

        # Answer each clarification and verify earlier answers persist.
        result = send_message_and_get_result("5")
        assert result["requirements"]["vcpus"] == 5
        assert result["requirements"]["region"] == "ap-south-1"

        result = send_message_and_get_result("8")
        assert result["requirements"]["memory_gb"] == 8
        assert result["requirements"]["vcpus"] == 5

        result = send_message_and_get_result("no")
        assert result["requirements"]["public_access"] is False
        assert result["requirements"]["memory_gb"] == 8

        result = send_message_and_get_result("Linux")
        assert result["status"] == "complete"
        assert result["requirements"]["operating_system"] == "Linux"
        assert result["requirements"]["cloud_provider"] == "aws"
        assert result["requirements"]["vcpus"] == 5
        assert result["requirements"]["memory_gb"] == 8
        assert result["requirements"]["public_access"] is False
