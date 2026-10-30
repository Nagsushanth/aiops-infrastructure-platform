
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser, AdminUser
from backend.app.core.dependencies import get_db
from backend.app.models.approval import (
    ApprovalAuditEvent,
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.schemas.approval import (
    ApprovalAuditResponse,
    ApprovalCreate,
    ApprovalDecision,
    ApprovalResponse,
    ApprovalStageDecision,
    ApprovalStageReviewResponse,
)
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
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


def _fingerprint_specification(
    specification: ValidatedInfrastructureSpecification,
) -> str:
    """Return SHA-256 for the canonical validated specification."""
    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()


def _is_sha256_fingerprint(value: str | None) -> bool:
    """Check that a fingerprint is a 64-character hexadecimal SHA-256."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )

def _has_eligible_security_analysis(plan_summary: object) -> bool:
    """Allow approval only when all required security checks pass."""

    if not isinstance(plan_summary, dict):
        return False

    analysis = plan_summary.get("security_analysis")

    if not isinstance(analysis, dict):
        return False

    severity_counts = analysis.get("severity_counts")
    findings = analysis.get("findings")
    finding_count = analysis.get("finding_count")

    expected_severities = {
        "critical",
        "high",
        "medium",
        "low",
    }

    return (
        analysis.get("checks_completed") is True
        and analysis.get("deployment_enabled") is False
        and analysis.get("passed") is True
        and type(finding_count) is int
        and finding_count == 0
        and isinstance(findings, list)
        and len(findings) == 0
        and isinstance(severity_counts, dict)
        and set(severity_counts.keys()) == expected_severities
        and all(
            type(severity_counts[key]) is int
            and severity_counts[key] == 0
            for key in expected_severities
        )
    )


APPROVAL_STAGES = ("technical", "cost", "security")


def _cost_review_requires_override(plan_summary: object) -> bool:
    """Require an explicit override when cost governance is incomplete."""

    if not isinstance(plan_summary, dict):
        return True

    estimate = plan_summary.get("cost_estimate")

    if not isinstance(estimate, dict):
        return True

    if estimate.get("status") != "estimated":
        return True

    return estimate.get("budget_status") in {
        "exceeds_threshold",
        "not_configured",
        "not_evaluated",
    }

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
    """Create an approval request linked to a trusted Terraform plan record."""

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
            detail=(
                "An approval request already exists for this "
                "infrastructure request."
            ),
        )

    plan_record = db.get(
        TerraformPlanRecord,
        payload.terraform_plan_record_id,
    )

    # Hide plans belonging to another user or infrastructure request.
    if (
        plan_record is None
        or plan_record.infrastructure_request_id != infrastructure_request.id
        or plan_record.requester_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trusted Terraform plan record not found.",
        )

    if plan_record.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only successfully generated Terraform plans "
                "can be submitted for approval."
            ),
        )

    if not _has_eligible_security_analysis(plan_record.plan_summary):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Terraform plan did not pass the required security "
                "checks and cannot be submitted for approval."
            ),
        )

    if not _is_sha256_fingerprint(plan_record.plan_fingerprint):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Terraform plan record has no valid fingerprint.",
        )

    if not _is_sha256_fingerprint(plan_record.specification_fingerprint):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Terraform plan specification fingerprint is invalid.",
        )

    # Revalidate the current requirements and ensure the plan is not stale.
    try:
        specification = ValidatedInfrastructureSpecification.model_validate(
            infrastructure_request.requirements
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Infrastructure requirements are no longer valid.",
        ) from exc

    current_fingerprint = _fingerprint_specification(specification)

    if plan_record.specification_fingerprint != current_fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Infrastructure requirements changed after plan generation. "
                "Generate a new Terraform plan."
            ),
        )

    approval = ApprovalRequest(
        infrastructure_request_id=infrastructure_request.id,
        terraform_plan_record_id=plan_record.id,
        requester_id=current_user.id,
        status="pending",
        plan_summary=plan_record.plan_summary,
        plan_fingerprint=plan_record.plan_fingerprint,
    )

    try:
        db.add(approval)
        db.flush()

                # Create independent review records for all three approval stages.
        stages = ("technical", "cost", "security")

        for stage in stages:
            db.add(
                ApprovalStageReview(
                    approval_request_id=approval.id,
                    stage=stage,
                    status="pending",
                )
            )

        db.add(
            ApprovalAuditEvent(
                approval_request_id=approval.id,
                actor_id=current_user.id,
                event_type="submitted",
                previous_status=None,
                new_status="pending",
                comments=None,
                event_metadata={
                    "source": "trusted_terraform_plan",
                    "terraform_plan_record_id": plan_record.id,
                    "plan_fingerprint": plan_record.plan_fingerprint,
                },
            )
        )

        db.commit()
        db.refresh(approval)
        return approval

    except Exception:
        db.rollback()
        raise


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
    "/{approval_id}/stages",
    response_model=list[ApprovalStageReviewResponse],
)
def get_approval_stages(
    approval_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    approval = get_approval_or_404(approval_id, db)
    ensure_can_view(approval, current_user.id, current_user.role)

    statement = (
        select(ApprovalStageReview)
        .where(
            ApprovalStageReview.approval_request_id == approval.id
        )
        .order_by(ApprovalStageReview.id)
    )

    return list(db.scalars(statement).all())


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
        .order_by(
            ApprovalAuditEvent.created_at,
            ApprovalAuditEvent.id,
        )
    )

    return list(db.scalars(statement).all())



@router.post(
    "/{approval_id}/stages/{stage}/decision",
    response_model=ApprovalStageReviewResponse,
)
def decide_approval_stage(
    approval_id: int,
    stage: str,
    payload: ApprovalStageDecision,
    reviewer: AdminUser,
    db: Session = Depends(get_db),
):
    if stage not in APPROVAL_STAGES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval stage not found.",
        )

    # Lock the parent first to serialize decisions across stages.
    approval_statement = (
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
    )
    approval = db.scalar(approval_statement)

    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found.",
        )

    if approval.requester_id == reviewer.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot review your own request.",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This approval request is no longer pending.",
        )

    stage_statement = (
        select(ApprovalStageReview)
        .where(
            ApprovalStageReview.approval_request_id == approval.id,
            ApprovalStageReview.stage == stage,
        )
        .with_for_update()
    )
    stage_review = db.scalar(stage_statement)

    if stage_review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval stage review not found.",
        )

    if stage_review.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This stage has already been decided.",
        )

    if (
        stage_review.assigned_reviewer_id is not None
        and stage_review.assigned_reviewer_id != reviewer.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This stage is assigned to another reviewer.",
        )

    comments = payload.comments.strip() if payload.comments else None

    # Manual overrides are only valid for an approved cost review.
    if payload.manual_cost_override:
        if stage != "cost" or payload.decision != "approved":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Manual cost override is only valid when approving "
                    "the cost review."
                ),
            )

        if not comments:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A documented reason is required for a cost override.",
            )

    # Never allow an incomplete or failed security analysis to pass review.
    if stage == "security" and payload.decision == "approved":
        if not _has_eligible_security_analysis(approval.plan_summary):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The Terraform plan has not passed security checks.",
            )

    # Require an explicit documented override for incomplete cost governance.
    if (
        stage == "cost"
        and payload.decision == "approved"
        and _cost_review_requires_override(approval.plan_summary)
        and not payload.manual_cost_override
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cost review requires a documented manual override "
                "because the estimate or budget evaluation is incomplete."
            ),
        )

    decided_at = datetime.now(timezone.utc)
    previous_stage_status = stage_review.status

    stage_review.status = payload.decision
    stage_review.decided_by_id = reviewer.id
    stage_review.comments = comments
    stage_review.decided_at = decided_at
    stage_review.updated_at = decided_at

    db.add(
        ApprovalAuditEvent(
            approval_request_id=approval.id,
            actor_id=reviewer.id,
            event_type=f"{stage}_{payload.decision}",
            previous_status=previous_stage_status,
            new_status=payload.decision,
            comments=comments,
            event_metadata={
                "source": "approval_stage_api",
                "stage": stage,
                "stage_review_id": stage_review.id,
                "manual_cost_override": (
                    payload.manual_cost_override
                ),
            },
        )
    )

    if payload.decision == "rejected":
        previous_approval_status = approval.status

        approval.status = "rejected"
        approval.reviewer_id = reviewer.id
        approval.reviewer_comments = comments
        approval.decided_at = decided_at
        approval.updated_at = decided_at

        db.add(
            ApprovalAuditEvent(
                approval_request_id=approval.id,
                actor_id=reviewer.id,
                event_type="approval_rejected_by_stage",
                previous_status=previous_approval_status,
                new_status="rejected",
                comments=comments,
                event_metadata={
                    "source": "approval_stage_api",
                    "stage": stage,
                    "stage_review_id": stage_review.id,
                },
            )
        )

    try:
        db.commit()
        db.refresh(stage_review)
        return stage_review
    except Exception:
        db.rollback()
        raise



@router.post("/{approval_id}/decision", response_model=ApprovalResponse)
def decide_approval(
    approval_id: int,
    payload: ApprovalDecision,
    reviewer: AdminUser,
    db: Session = Depends(get_db),
):
    # Lock the approval row so concurrent decisions cannot both succeed.
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

    if payload.decision == "approved":
        stage_statement = select(ApprovalStageReview).where(
            ApprovalStageReview.approval_request_id == approval.id
        )
        stage_reviews = list(db.scalars(stage_statement).all())

        stage_statuses = {
            review.stage: review.status
            for review in stage_reviews
        }

        if (
            set(stage_statuses) != set(APPROVAL_STAGES)
            or any(
                stage_statuses.get(stage) != "approved"
                for stage in APPROVAL_STAGES
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Technical, cost and security reviews must all "
                    "be approved before final approval."
                ),
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

    try:
        db.commit()
        db.refresh(approval)
        return approval
    except Exception:
        db.rollback()
        raise
