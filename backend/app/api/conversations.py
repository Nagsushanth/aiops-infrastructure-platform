
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.models.message_attachment import MessageAttachment
from backend.app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
)
from backend.app.schemas.message import MessageCreate, MessageResponse
from backend.app.schemas.attachment import (
    AttachmentResponse,
    AttachmentUploadResponse,
)
from backend.app.services.attachments import (
    UPLOAD_DIRECTORY,
    store_attachment,
    delete_stored_attachment,
)

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def get_owned_conversation(
    conversation_id: int,
    user_id: int,
    db: Session,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    conversation = db.scalar(statement)

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return conversation


@router.get("/by-session/{session_id}", response_model=ConversationResponse)
def get_conversation_by_session(
    session_id: str,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    statement = select(Conversation).where(
        Conversation.session_id == session_id,
        Conversation.user_id == current_user.id,
    )
    conversation = db.scalar(statement)

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return conversation


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: ConversationCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    conversation = Conversation(
        user_id=current_user.id,
        session_id=payload.session_id,
        title=payload.title,
    )
    db.add(conversation)

    try:
        db.commit()
        db.refresh(conversation)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A conversation with this session_id already exists.",
        )

    return conversation


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(
        conversation_id, current_user.id, db
    )

    message = Message(
        conversation_id=conversation.id,
        role=payload.role,
        content=payload.content,
    )
    db.add(message)

    conversation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(message)

    return message


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
def get_messages(
    conversation_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    get_owned_conversation(conversation_id, current_user.id, db)

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )

    return list(db.scalars(statement).all())


@router.post(
    "/{conversation_id}/attachments",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: int,
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(
        conversation_id,
        current_user.id,
        db,
    )

    stored_file = await store_attachment(file)

    try:
        message_content = f"📎 {stored_file['original_filename']}"

        message = Message(
            conversation_id=conversation.id,
            role="user",
            content=message_content,
        )
        db.add(message)
        db.flush()

        attachment = MessageAttachment(
            conversation_id=conversation.id,
            message_id=message.id,
            uploaded_by=current_user.id,
            original_filename=stored_file["original_filename"],
            storage_filename=stored_file["storage_filename"],
            content_type=stored_file["content_type"],
            size_bytes=stored_file["size_bytes"],
            sha256=stored_file["sha256"],
        )

        db.add(attachment)
        conversation.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(attachment)
        db.refresh(message)

        return {
            "attachment": attachment,
            "message_id": message.id,
            "message_content": message.content,
        }

    except Exception:
        db.rollback()

        try:
            delete_stored_attachment(stored_file["storage_filename"])
        except OSError:
            pass

        raise


@router.get(
    "/{conversation_id}/attachments",
    response_model=list[AttachmentResponse],
)
def list_attachments(
    conversation_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    get_owned_conversation(
        conversation_id,
        current_user.id,
        db,
    )

    statement = (
        select(MessageAttachment)
        .where(
            MessageAttachment.conversation_id == conversation_id,
        )
        .order_by(
            MessageAttachment.created_at,
            MessageAttachment.id,
        )
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{conversation_id}/attachments/{attachment_id}",
)
def download_attachment(
    conversation_id: int,
    attachment_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    get_owned_conversation(
        conversation_id,
        current_user.id,
        db,
    )

    statement = select(MessageAttachment).where(
        MessageAttachment.id == attachment_id,
        MessageAttachment.conversation_id == conversation_id,
    )

    attachment = db.scalar(statement)

    if attachment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found.",
        )

    storage_filename = attachment.storage_filename

    if (
        not storage_filename
        or Path(storage_filename).name != storage_filename
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid attachment storage reference.",
        )

    storage_path = (
        UPLOAD_DIRECTORY / storage_filename
    ).resolve()

    if storage_path.parent != UPLOAD_DIRECTORY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid attachment storage path.",
        )

    if not storage_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment file not found.",
        )

    return FileResponse(
        path=storage_path,
        media_type=attachment.content_type,
        filename=attachment.original_filename,
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
        },
    )
