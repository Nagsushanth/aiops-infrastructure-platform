
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
)
from backend.app.schemas.message import MessageCreate, MessageResponse

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
