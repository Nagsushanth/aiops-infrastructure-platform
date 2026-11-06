"""Authenticated infrastructure lifecycle operation API."""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db

from backend.app.models.approval import (
    ApprovalAuditEvent,
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_operation import (
    InfrastructureOperation,
)
from backend.app.models.infrastructure_request import (
    InfrastructureRequest,
)
from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.models.terraform_state_version import TerraformStateVersion
from backend.app.models.terraform_plan import TerraformPlanRecord

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.schemas.infrastructure_operation import (
    InfrastructureOperationCreate,
    InfrastructureOperationResponse,
    TerraformStateVersionResponse,
    TerraformStateVersionSyncResponse,
)

from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    load_plan_artifact,
)
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    create_s3_client,
    sync_s3_state_versions,
)
from backend.app.services.terraform.state_version_manager import (
    TerraformStateVersionError,
    get_state_versions,
)

router = APIRouter(
    prefix="/infrastructure-operations",
    tags=["Infrastructure Operations"],
)

APPROVAL_STAGES = {"technical", "cost", "security"}


def get_owned_infrastructure_request(
    infrastructure_request_id: int,
    current_user_id: int,
    db: Session,
) -> InfrastructureRequest:
    """Return a request only when it belongs to the current user."""

    infrastructure_request = db.get(
        InfrastructureRequest,
        infrastructure_request_id,
    )

    if infrastructure_request is None:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure request not found.",
        )

    conversation = db.get(
        Conversation,
        infrastructure_request.conversation_id,
    )

    if conversation is None or conversation.user_id != current_user_id:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure request not found.",
        )

    return infrastructure_request


def fingerprint_specification(
    specification: ValidatedInfrastructureSpecification,
) -> str:
    """Use the same canonical JSON representation as the approval workflow."""

    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()


def is_valid_fingerprint(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(
            character in "0123456789abcdef"
            for character in value.lower()
        )
    )


def validate_approved_plan(
    approval: ApprovalRequest,
    current_user_id: int,
    db: Session,
):
    """Validate ownership, approval stages, plan integrity and freshness."""

    if approval.requester_id != current_user_id:
        raise HTTPException(
            status_code=404,
            detail="Approval request not found.",
        )

    if approval.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="The approval request has not been finally approved.",
        )

    if approval.terraform_plan_record_id is None:
        raise HTTPException(
            status_code=409,
            detail="The approval is not linked to a Terraform plan.",
        )

    infrastructure_request = get_owned_infrastructure_request(
        approval.infrastructure_request_id,
        current_user_id,
        db,
    )

    plan = db.get(
        TerraformPlanRecord,
        approval.terraform_plan_record_id,
    )

    if (
        plan is None
        or plan.infrastructure_request_id != infrastructure_request.id
        or plan.requester_id != current_user_id
    ):
        raise HTTPException(
            status_code=404,
            detail="Trusted Terraform plan not found.",
        )

    if plan.status != "succeeded":
        raise HTTPException(
            status_code=409,
            detail="Only successful Terraform plans can be provisioned.",
        )

    if not is_valid_fingerprint(plan.plan_fingerprint):
        raise HTTPException(
            status_code=409,
            detail="Terraform plan fingerprint is invalid.",
        )

    if approval.plan_fingerprint != plan.plan_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="The approved plan fingerprint does not match.",
        )

    if not is_valid_fingerprint(plan.specification_fingerprint):
        raise HTTPException(
            status_code=409,
            detail="Terraform specification fingerprint is invalid.",
        )

    try:
        specification = (
            ValidatedInfrastructureSpecification.model_validate(
                infrastructure_request.requirements
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail="Infrastructure requirements are no longer valid.",
        ) from exc

    current_fingerprint = fingerprint_specification(specification)

    if plan.specification_fingerprint != current_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=(
                "Infrastructure requirements changed after plan generation. "
                "Generate a new Terraform plan and obtain approval again."
            ),
        )

    reviews = list(
        db.scalars(
            select(ApprovalStageReview).where(
                ApprovalStageReview.approval_request_id == approval.id
            )
        ).all()
    )

    stage_statuses = {
        review.stage: review.status
        for review in reviews
    }

    if (
        set(stage_statuses) != APPROVAL_STAGES
        or any(
            stage_statuses.get(stage) != "approved"
            for stage in APPROVAL_STAGES
        )
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Technical, cost and security reviews must all "
                "be approved before provisioning."
            ),
        )

    if not plan.artifact_path or not plan.artifact_sha256:
        raise HTTPException(
            status_code=409,
            detail="The Terraform plan artifact is unavailable.",
        )

    try:
        load_plan_artifact(
            plan.artifact_path,
            plan.artifact_sha256,
        )
    except TerraformArtifactError as exc:
        raise HTTPException(
            status_code=409,
            detail="Terraform plan artifact integrity verification failed.",
        ) from exc

    return infrastructure_request, plan


@router.post(
    "/provision",
    response_model=InfrastructureOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_provision_operation(
    payload: InfrastructureOperationCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Queue a provision operation after validating its approved plan."""

    approval = db.get(
        ApprovalRequest,
        payload.approval_request_id,
    )

    if approval is None or approval.requester_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Approval request not found.",
        )

    infrastructure_request, plan = validate_approved_plan(
        approval,
        current_user.id,
        db,
    )

    # Prevent reusing the same approval for multiple provision operations.
    existing_operation = db.scalar(
        select(InfrastructureOperation).where(
            InfrastructureOperation.approval_request_id == approval.id,
            InfrastructureOperation.operation == "provision",
        )
    )

    if existing_operation is not None:
        raise HTTPException(
            status_code=409,
            detail="A provision operation already exists for this approval.",
        )

    operation = InfrastructureOperation(
        infrastructure_request_id=infrastructure_request.id,
        terraform_plan_record_id=plan.id,
        approval_request_id=approval.id,
        requester_id=current_user.id,
        operation="provision",
        status="queued",
        comments=payload.comments,
        result_summary={
            "message": "Provisioning operation queued for preflight.",
            "terraform_apply_enabled": False,
        },
    )

    try:
        db.add(operation)
        db.flush()

        db.add(
            ApprovalAuditEvent(
                approval_request_id=approval.id,
                actor_id=current_user.id,
                event_type="provision_requested",
                previous_status="approved",
                new_status="approved",
                comments=payload.comments,
                event_metadata={
                    "source": "infrastructure_operations_api",
                    "operation_id": operation.id,
                    "terraform_plan_record_id": plan.id,
                },
            )
        )

        db.commit()
        db.refresh(operation)

    except Exception:
        db.rollback()
        raise

    # Dispatch only after the database transaction has committed.
    try:
        from backend.app.workers.tasks import (
            execute_provision_operation,
        )

        task = execute_provision_operation.delay(operation.id)

        operation.celery_task_id = task.id
        db.commit()
        db.refresh(operation)

    except Exception:
        db.rollback()

        operation = db.get(
            InfrastructureOperation,
            operation.id,
        )

        if operation is not None:
            operation.status = "failed"
            operation.error_code = "CELERY_DISPATCH_FAILED"
            operation.completed_at = datetime.now(timezone.utc)
            operation.result_summary = {
                "message": "The background worker could not be reached.",
                "terraform_apply_enabled": False,
            }
            db.commit()
            db.refresh(operation)

    return operation


@router.get(
    "",
    response_model=list[InfrastructureOperationResponse],
)
def list_operations(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """List operations belonging to the authenticated user."""

    statement = (
        select(InfrastructureOperation)
        .where(
            InfrastructureOperation.requester_id == current_user.id
        )
        .order_by(InfrastructureOperation.created_at.desc())
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{operation_id}",
    response_model=InfrastructureOperationResponse,
)
def get_operation(
    operation_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Get one operation belonging to the authenticated user."""

    operation = db.get(
        InfrastructureOperation,
        operation_id,
    )

    if operation is None or operation.requester_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure operation not found.",
        )

    return operation


@router.get("/{operation_id}/audit")
def get_operation_audit(
    operation_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return approval audit history for this operation."""

    operation = db.get(
        InfrastructureOperation,
        operation_id,
    )

    if operation is None or operation.requester_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure operation not found.",
        )

    events = db.scalars(
        select(ApprovalAuditEvent)
        .where(
            ApprovalAuditEvent.approval_request_id
            == operation.approval_request_id
        )
        .order_by(
            ApprovalAuditEvent.created_at,
            ApprovalAuditEvent.id,
        )
    ).all()

    return [
        {
            "id": event.id,
            "approval_request_id": event.approval_request_id,
            "actor_id": event.actor_id,
            "event_type": event.event_type,
            "previous_status": event.previous_status,
            "new_status": event.new_status,
            "comments": event.comments,
            "event_metadata": event.event_metadata,
            "created_at": event.created_at,
        }
        for event in events
    ]

@router.post(
    "/requests/{infrastructure_request_id}/state-versions/sync",
    response_model=TerraformStateVersionSyncResponse,
)
def synchronize_state_versions(
    infrastructure_request_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Synchronize S3 Terraform state-version history."""

    get_owned_infrastructure_request(
        infrastructure_request_id,
        current_user.id,
        db,
    )

    state_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if state_record is None:
        raise HTTPException(
            status_code=404,
            detail="Terraform state record not found.",
        )

    if state_record.backend_type != "s3":
        raise HTTPException(
            status_code=409,
            detail="Only S3 state backends are supported.",
        )
    if state_record.status == "archived":
        raise HTTPException(
            status_code=409,
            detail="Archived Terraform state cannot be synchronized.",
        )

    try:
        client = create_s3_client(
            region_name=state_record.state_region,
        )

        result = sync_s3_state_versions(
            db,
            client,
            infrastructure_request_id,
            state_record.state_bucket,
            state_record.state_key,
        )

        versions = get_state_versions(
            db,
            infrastructure_request_id,
        )

    except S3StateStorageError as exc:
        raise HTTPException(
            status_code=502,
            detail="Terraform state synchronization failed.",
        ) from exc

    except TerraformStateVersionError as exc:
        raise HTTPException(
            status_code=409,
            detail="Terraform state version retrieval failed.",
        ) from exc

    return {
        "infrastructure_request_id": infrastructure_request_id,
        **result,
        "versions": versions,
    }
