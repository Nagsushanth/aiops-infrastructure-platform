
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.core.security import decode_access_token
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.messages import Message
from backend.app.models.user import User
from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.requirements.gathering import gather_requirements

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])

AUTH_TIMEOUT_SECONDS = 10


@router.websocket("/ws/conversations/{session_id}")
async def conversation_websocket(
    websocket: WebSocket,
    session_id: str,
):
    db = SessionLocal()

    try:
        # Accept the connection, but do not allow chat activity yet.
        await websocket.accept()

        # The browser WebSocket API cannot set an Authorization header,
        # so require a JWT in the first WebSocket message.
        try:
            auth_data = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await websocket.send_json({
                "type": "error",
                "detail": "Authentication timed out. Please reconnect.",
            })
            await websocket.close(code=4408)
            return

        if (
            not isinstance(auth_data, dict)
            or auth_data.get("type") != "authenticate"
            or not isinstance(auth_data.get("token"), str)
            or not auth_data["token"].strip()
        ):
            await websocket.send_json({
                "type": "error",
                "detail": "Authentication is required.",
            })
            await websocket.close(code=4401)
            return

        # Validate the same JWT format used by the HTTP authentication flow.
        payload = decode_access_token(auth_data["token"])

        if not payload:
            await websocket.send_json({
                "type": "error",
                "detail": "Invalid or expired authentication token.",
            })
            await websocket.close(code=4401)
            return

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.isdigit():
            await websocket.send_json({
                "type": "error",
                "detail": "Invalid authentication token.",
            })
            await websocket.close(code=4401)
            return

        current_user = db.get(User, int(subject))

        if current_user is None or not current_user.is_active:
            await websocket.send_json({
                "type": "error",
                "detail": "User is inactive or no longer exists.",
            })
            await websocket.close(code=4401)
            return

        # A user may connect only to their own conversation.
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.session_id == session_id,
                Conversation.user_id == current_user.id,
            )
        )

        if conversation is None:
            await websocket.send_json({
                "type": "error",
                "detail": "Conversation not found or access denied.",
            })
            await websocket.close(code=4404)
            return

        # Authentication and ownership are verified. Chat can begin.
        await websocket.send_json({
            "type": "connected",
            "conversation_id": conversation.id,
            "message": "WebSocket connected successfully.",
        })

        while True:
            data = await websocket.receive_json()

            if not isinstance(data, dict):
                await websocket.send_json({
                    "type": "error",
                    "detail": "Invalid message format.",
                })
                continue

            message_type = data.get("type")

            if message_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if message_type != "message":
                await websocket.send_json({
                    "type": "error",
                    "detail": "Unsupported message type.",
                })
                continue

            content = data.get("content")

            if not isinstance(content, str) or not content.strip():
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message content cannot be empty.",
                })
                continue

            content = content.strip()

            if len(content) > 50000:
                await websocket.send_json({
                    "type": "error",
                    "detail": "Message exceeds the 50000-character limit.",
                })
                continue

            # Save the user's message.
            saved_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=content,
            )
            db.add(saved_message)
            conversation.updated_at = datetime.now(timezone.utc)
            db.flush()

            # Load previously collected requirements.
            request_record = db.scalar(
                select(InfrastructureRequest).where(
                    InfrastructureRequest.conversation_id
                    == conversation.id
                )
            )

            current = None
            if request_record is not None:
                # A completed request must not be reused for a new request.
                # Start fresh so the new message is parsed and follow-up
                # questions begin from its own requirements.
                if request_record.status != "complete":
                    current = InfrastructureRequirements.model_validate(
                        request_record.requirements
                    )

            logger.info(
    "Requirement input: conversation_id=%s, content=%r, current=%s",
    conversation.id,
    content,
    current.model_dump() if current is not None else None,
)
            # Keep the existing deterministic requirements service.
            result = gather_requirements(content, current)

            logger.info(
    "Requirement result: conversation_id=%s, status=%s, requirements=%s",
    conversation.id,
    result["status"],
    result["requirements"],
)

            if request_record is None:
                request_record = InfrastructureRequest(
                    conversation_id=conversation.id,
                    status=result["status"],
                    requirements=result["requirements"],
                )
                db.add(request_record)
            else:
                request_record.status = result["status"]
                request_record.requirements = result["requirements"]

            # Commit the user's message and requirement state together.
            db.commit()
            db.refresh(saved_message)

            await websocket.send_json({
                "type": "message_saved",
                "message": {
                    "id": saved_message.id,
                    "conversation_id": saved_message.conversation_id,
                    "role": saved_message.role,
                    "content": saved_message.content,
                    "created_at": saved_message.created_at.isoformat(),
                },
            })

            await websocket.send_json({
                "type": "requirements_result",
                "result": {
                    **result,
                    "infrastructure_request_id": request_record.id,
                },
            })

            # Save and return a clarification, or confirm a completed request.
            assistant_text = result.get("question")
            if not assistant_text and result.get("status") == "complete":
                requirements = result["requirements"]
                access_text = (
                    "publicly accessible"
                    if requirements["public_access"]
                    else "private"
                )
                assistant_text = (
                    "Your infrastructure requirements are complete. "
                    f"I recorded a {requirements['environment']} "
                    f"{requirements['resource_type']} request for "
                    f"{requirements['cloud_provider'].upper()} in "
                    f"{requirements['region']} with "
                    f"{requirements['vcpus']} vCPUs, "
                    f"{requirements['memory_gb']} GB memory, and "
                    f"{access_text} access."
                )

            if assistant_text:
                assistant_message = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=assistant_text,
                )
                db.add(assistant_message)
                conversation.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(assistant_message)

                await websocket.send_json({
                    "type": "assistant_message",
                    "message": {
                        "id": assistant_message.id,
                        "conversation_id": assistant_message.conversation_id,
                        "role": assistant_message.role,
                        "content": assistant_message.content,
                        "created_at": assistant_message.created_at.isoformat(),
                    },
                })

    except WebSocketDisconnect:
        pass

    except Exception:
        db.rollback()
        logger.exception("WebSocket conversation processing failed")
        try:
            await websocket.send_json({
                "type": "error",
                "detail": "An internal server error occurred.",
            })
        except Exception:
            pass

    finally:
        db.close()
