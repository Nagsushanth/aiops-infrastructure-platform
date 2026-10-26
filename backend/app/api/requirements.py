
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.schemas.requirement_request import (
    GatherRequirementsRequest,
    GatherRequirementsResponse,
)
from backend.app.services.requirements.gathering import gather_requirements


router = APIRouter(prefix="/requirements", tags=["Requirements"])


@router.post(
    "/gather",
    response_model=GatherRequirementsResponse,
)
def gather_infrastructure_requirements(
    payload: GatherRequirementsRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.session_id == payload.session_id,
            Conversation.user_id == current_user.id,
        )
    )

    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    request_record = db.scalar(
        select(InfrastructureRequest).where(
            InfrastructureRequest.conversation_id == conversation.id
        )
    )

    current = None
    if request_record is not None:
        try:
            current = InfrastructureRequirements.model_validate(
                request_record.requirements
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stored infrastructure requirements are invalid.",
            ) from exc

    result = gather_requirements(payload.message, current)

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

    try:
        db.commit()
        db.refresh(request_record)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save infrastructure requirements.",
        ) from exc

    return {
    **result,
    "infrastructure_request_id": request_record.id,
}
