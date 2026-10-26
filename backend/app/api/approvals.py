from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser, AdminUser
from backend.app.core.dependencies import get_db
from backend.app.models.approval import ApprovalAuditEvent, ApprovalRequest
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.schemas.approval import (
    ApprovalAuditResponse,
    ApprovalCreate,
    ApprovalDecision,
    ApprovalResponse,
)
import hashlib
import json

from backend.app.services.terraform.plan_parser import (
    TerraformPlanParsingError,
    parse_terraform_plan,
)

router = APIRouter(prefix="/approvals", tags=["Approvals"])


def get_approval_or_404(
    approval_id: int,
    db: Session,
) -> ApprovalRequest:
    approval = db.get(ApprovalRequest, approval_id)

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    return approval


def ensure_can_view(
    approval: ApprovalRequest,
    user_id: int,
    role: str,
) -> None:
    if role != "admin" and approval.requester_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )


@router.post(
    "",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_approval(
    payload: ApprovalCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    infrastructure_request = db.get(
        InfrastructureRequest,
        payload.infrastructure_request_id,
    )

    if infrastructure_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Infrastructure request not found.",
        )

    conversation = db.get(
        Conversation,
        infrastructure_request.conversation_id,
    )

    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Infrastructure request not found.",
        )

    if infrastructure_request.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Infrastructure requirements must be complete "
                "before requesting approval."
            ),
        )

    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.infrastructure_request_id
            == infrastructure_request.id
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An approval request already exists for this infrastructure request.",
        )

    try:
        parsed_plan = parse_terraform_plan(payload.plan)
    except TerraformPlanParsingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Terraform plan: {exc}",
        ) from exc

    # Require a plan that is explicitly marked as complete and applyable.
    if (
        payload.plan.get("errored") is not False
        or payload.plan.get("complete") is not True
        or payload.plan.get("applyable") is not True
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The plan must be complete, applyable, and free of errors.",
        )

    canonical_plan = json.dumps(
        payload.plan,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    plan_fingerprint = hashlib.sha256(
        canonical_plan.encode("utf-8")
    ).hexdigest()

    approval = ApprovalRequest(
        infrastructure_request_id=infrastructure_request.id,
        requester_id=current_user.id,
        status="pending",
        plan_summary=parsed_plan,
        plan_fingerprint=plan_fingerprint,
    )

    db.add(approval)
    db.flush()

    db.add(
        ApprovalAuditEvent(
            approval_request_id=approval.id,
            actor_id=current_user.id,
            event_type="submitted",
            previous_status=None,
            new_status="pending",
            comments=None,
            event_metadata={"source": "approval_api","plan_fingerprint": plan_fingerprint,},
        )
    )

    db.commit()
    db.refresh(approval)
    return approval


@router.get("", response_model=list[ApprovalResponse])
def list_approvals(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    statement = select(ApprovalRequest)

    if current_user.role != "admin":
        statement = statement.where(
            ApprovalRequest.requester_id == current_user.id
        )

    statement = statement.order_by(ApprovalRequest.created_at.desc())
    return list(db.scalars(statement).all())


@router.get("/{approval_id}", response_model=ApprovalResponse)
def get_approval(
    approval_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    approval = get_approval_or_404(approval_id, db)
    ensure_can_view(approval, current_user.id, current_user.role)
    return approval


@router.get(
    "/{approval_id}/audit",
    response_model=list[ApprovalAuditResponse],
)
def get_approval_audit(
    approval_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    approval = get_approval_or_404(approval_id, db)
    ensure_can_view(approval, current_user.id, current_user.role)

    statement = (
        select(ApprovalAuditEvent)
        .where(ApprovalAuditEvent.approval_request_id == approval.id)
        .order_by(ApprovalAuditEvent.created_at, ApprovalAuditEvent.id)
    )

    return list(db.scalars(statement).all())


@router.post("/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(
    approval_id: int,
    payload: ApprovalDecision,
    reviewer: AdminUser,
    db: Session = Depends(get_db),
):
    # Lock the approval row so simultaneous decisions cannot both succeed.
    statement = (
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
    )
    approval = db.scalar(statement)

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    if approval.requester_id == reviewer.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot approve or reject your own request.",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This approval request has already been decided.",
        )

    previous_status = approval.status
    decided_at = datetime.now(timezone.utc)

    approval.status = payload.decision
    approval.reviewer_id = reviewer.id
    approval.reviewer_comments = payload.comments
    approval.decided_at = decided_at
    approval.updated_at = decided_at

    db.add(
        ApprovalAuditEvent(
            approval_request_id=approval.id,
            actor_id=reviewer.id,
            event_type=payload.decision,
            previous_status=previous_status,
            new_status=payload.decision,
            comments=payload.comments,
            event_metadata={"source": "approval_api"},
        )
    )

    db.commit()
    db.refresh(approval)
    return approval
