"""Authenticated infrastructure lifecycle operation API."""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import (
    AdminUser,
    CurrentUser,
)
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
    InfrastructureInventorySyncResponse,
    InfrastructureOperationCreate,
    InfrastructureOperationResponse,
    TerraformStateVersionResponse,
    TerraformStateVersionSyncResponse,
    InfrastructureDeprovisionCreate,
    InfrastructureRecoveryResolution,
)

from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    load_plan_artifact,
)
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    read_state_object,
    parse_terraform_state,
    create_s3_client,
    sync_s3_state_versions,
)
from backend.app.services.terraform.inventory_manager import (
    InfrastructureInventoryError,
    sync_infrastructure_inventory,
)
from backend.app.services.terraform.state_version_manager import (
    TerraformStateVersionError,
    get_state_versions,
)
from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)
from backend.app.models.infrastructure_resource import (
    InfrastructureResource,
)
from backend.app.services.terraform.resource_health import (
    ResourceHealthError,
    create_ec2_client,
    get_ec2_resource_health,
)
from backend.app.services.terraform.operation_safety import (
    has_unresolved_recovery_operation,
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

def is_duplicate_operation_integrity_error(
    exc: IntegrityError,
) -> bool:
    """Identify only the infrastructure-operation uniqueness violation."""

    constraint_name = getattr(
        getattr(exc.orig, "diag", None),
        "constraint_name",
        None,
    )

    if (
        constraint_name
        == "uq_infrastructure_operation_approval_operation"
    ):
        return True

    # SQLite fallback for tests.
    error_message = str(exc.orig)

    return (
        "UNIQUE constraint failed: "
        "infrastructure_operations.approval_request_id, "
        "infrastructure_operations.operation"
    ) in error_message


def validate_approved_plan(
    approval: ApprovalRequest,
    current_user_id: int,
    db: Session,
    expected_operation: str = "provision",
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

    if plan.operation != expected_operation:
        detail = {
            "provision": "Only provisioning plans can be provisioned.",
            "deprovision": "Only deprovision plans can be used for destruction.",
        }.get(
            expected_operation,
            "Terraform plan operation does not match the requested operation.",
        )

        raise HTTPException(
            status_code=409,
            detail=detail,
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

    if (
        not plan.artifact_path
        or not is_valid_fingerprint(plan.artifact_sha256)
    ):
        raise HTTPException(
            status_code=409,
            detail="The Terraform plan artifact is unavailable.",
        )

    if plan.plan_fingerprint != plan.artifact_sha256:
        raise HTTPException(
            status_code=409,
            detail="Terraform plan fingerprint does not match its artifact.",
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
    if has_unresolved_recovery_operation(
        db,
        infrastructure_request.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Provisioning is blocked because an earlier infrastructure "
                "operation requires manual recovery review."
            ),
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

    except IntegrityError as exc:
        db.rollback()

        if is_duplicate_operation_integrity_error(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A provision operation already exists "
                    "for this approval."
                ),
            ) from exc

        raise

    except Exception:
        db.rollback()
        raise

    # Dispatch only after the database transaction has committed.
    operation_id = operation.id

    try:
        from backend.app.workers.tasks import execute_provision_operation

        task = execute_provision_operation.delay(operation_id)

    except Exception:
        # The broker did not accept the task. Record the failure and its
        # audit event together so the operation history remains consistent.
        db.rollback()

        operation = db.get(InfrastructureOperation, operation_id)

        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The operation record could not be reloaded after dispatch failure.",
            )

        operation.status = "failed"
        operation.error_code = "CELERY_DISPATCH_FAILED"
        operation.completed_at = datetime.now(timezone.utc)
        operation.result_summary = {
            "message": "The background worker could not be reached.",
            "terraform_apply_enabled": False,
            "automatic_retry": False,
        }

        db.add(
            InfrastructureOperationEvent(
                operation_id=operation.id,
                actor_id=None,
                actor_type="system",
                event_type="celery_dispatch_failed",
                previous_status="queued",
                new_status="failed",
                comments="The background worker could not be reached.",
                event_metadata={
                    "source": "infrastructure_operations_api",
                    "error_code": "CELERY_DISPATCH_FAILED",
                    "automatic_retry": False,
                    "terraform_apply_enabled": False,
                },
            )
        )

        db.commit()
        db.refresh(operation)
        return operation

    # The broker accepted the task. Persist the Celery task ID separately.
    # If this database write fails, do not mark the operation failed: the
    # worker may already have received or started the task.
    operation.celery_task_id = task.id
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


@router.post(
    "/deprovision",
    response_model=InfrastructureOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_deprovision_operation(
    payload: InfrastructureDeprovisionCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Queue a confirmed deprovision operation."""

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
        expected_operation="deprovision",
    )

    # Prevent reusing the same approval for multiple destroy operations.
    existing_operation = db.scalar(
        select(InfrastructureOperation).where(
            InfrastructureOperation.approval_request_id == approval.id,
            InfrastructureOperation.operation == "deprovision",
        )
    )

    if existing_operation is not None:
        raise HTTPException(
            status_code=409,
            detail="A deprovision operation already exists for this approval.",
        )

    operation = InfrastructureOperation(
        infrastructure_request_id=infrastructure_request.id,
        terraform_plan_record_id=plan.id,
        approval_request_id=approval.id,
        requester_id=current_user.id,
        operation="deprovision",
        status="queued",
        comments=payload.comments,
        result_summary={
            "message": "Destroy operation queued for preflight.",
            "destroy_confirmed": True,
            "terraform_destroy_enabled": False,
        },
    )

    try:
        db.add(operation)
        db.flush()

        db.add(
            ApprovalAuditEvent(
                approval_request_id=approval.id,
                actor_id=current_user.id,
                event_type="deprovision_requested",
                previous_status="approved",
                new_status="approved",
                comments=payload.comments,
                event_metadata={
                    "source": "infrastructure_operations_api",
                    "operation_id": operation.id,
                    "terraform_plan_record_id": plan.id,
                    "destroy_confirmed": True,
                },
            )
        )

        db.commit()
        db.refresh(operation)

    except IntegrityError as exc:
        db.rollback()

        if is_duplicate_operation_integrity_error(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A deprovision operation already exists "
                    "for this approval."
                ),
            ) from exc

        raise

    except Exception:
        db.rollback()
        raise

    # Dispatch only after the database transaction has committed.
    operation_id = operation.id

    try:
        from backend.app.workers.tasks import (
            execute_deprovision_operation,
        )

        task = execute_deprovision_operation.delay(operation_id)

    except Exception:
        db.rollback()

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        if operation is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "The operation record could not be reloaded "
                    "after dispatch failure."
                ),
            )

        operation.status = "failed"
        operation.error_code = "CELERY_DISPATCH_FAILED"
        operation.completed_at = datetime.now(timezone.utc)
        operation.result_summary = {
            "message": "The background worker could not be reached.",
            "destroy_confirmed": True,
            "terraform_destroy_enabled": False,
            "automatic_retry": False,
        }

        db.add(
            InfrastructureOperationEvent(
                operation_id=operation.id,
                actor_id=None,
                actor_type="system",
                event_type="celery_dispatch_failed",
                previous_status="queued",
                new_status="failed",
                comments="The background worker could not be reached.",
                event_metadata={
                    "source": "infrastructure_operations_api",
                    "error_code": "CELERY_DISPATCH_FAILED",
                    "automatic_retry": False,
                    "terraform_destroy_enabled": False,
                },
            )
        )

        db.commit()
        db.refresh(operation)
        return operation

    operation.celery_task_id = task.id
    db.commit()
    db.refresh(operation)

    return operation


@router.get("/{operation_id}/audit")
def get_operation_audit(
    operation_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Return approval and infrastructure-operation audit history."""
    operation = db.get(
        InfrastructureOperation,
        operation_id,
    )

    if operation is None or operation.requester_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure operation not found.",
        )

    approval_events = db.scalars(
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

    operation_events = db.scalars(
        select(InfrastructureOperationEvent)
        .where(
            InfrastructureOperationEvent.operation_id == operation.id
        )
        .order_by(
            InfrastructureOperationEvent.created_at,
            InfrastructureOperationEvent.id,
        )
    ).all()

    events = []

    for event in approval_events:
        events.append(
            {
                "id": event.id,
                "source": "approval",
                "approval_request_id": event.approval_request_id,
                "operation_id": operation.id,
                "actor_id": event.actor_id,
                "actor_type": "user",
                "event_type": event.event_type,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "comments": event.comments,
                "event_metadata": event.event_metadata,
                "created_at": event.created_at,
            }
        )

    for event in operation_events:
        events.append(
            {
                "id": event.id,
                "source": "infrastructure_operation",
                "approval_request_id": operation.approval_request_id,
                "operation_id": event.operation_id,
                "actor_id": event.actor_id,
                "actor_type": event.actor_type,
                "event_type": event.event_type,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "comments": event.comments,
                "event_metadata": event.event_metadata,
                "created_at": event.created_at,
            }
        )

    def audit_sort_key(event):
        timestamp = event["created_at"]

        # SQLite can return naive datetimes in tests.
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return (
            timestamp.astimezone(timezone.utc),
            event["source"],
            event["id"],
        )

    events.sort(key=audit_sort_key)

    return events


@router.post("/{operation_id}/resolve-recovery")
def resolve_operation_recovery(
    operation_id: int,
    payload: InfrastructureRecoveryResolution,
    reviewer: AdminUser,
    db: Session = Depends(get_db),
):
    """Record an admin decision resolving a manual recovery review."""

    statement = (
        select(InfrastructureOperation)
        .where(InfrastructureOperation.id == operation_id)
        .with_for_update()
    )
    operation = db.scalar(statement)

    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Infrastructure operation not found.",
        )

    if operation.requester_id == reviewer.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot review your own infrastructure operation.",
        )

    summary = operation.result_summary or {}

    if not isinstance(summary, dict):
        summary = {}

    requires_review = (
        summary.get("recovery_required") is True
        or summary.get("manual_review_required") is True
        or summary.get("recovery_status") == "MANUAL_REVIEW_REQUIRED"
    )

    if not requires_review:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This operation does not require manual recovery review.",
        )

    if operation.status not in {"failed", "blocked"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or blocked operations can be reviewed.",
        )

    existing_resolution = db.scalar(
        select(InfrastructureOperationEvent.id).where(
            InfrastructureOperationEvent.operation_id == operation.id,
            InfrastructureOperationEvent.event_type
            == "recovery_review_resolved",
        )
    )

    if existing_resolution is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Recovery review has already been resolved.",
        )

    if (
        summary.get("recovery_required") is True
        and payload.verification_scope != "state_and_cloud_reconciled"
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Operations requiring Terraform recovery must have "
                "their cloud resources and state reconciled."
            ),
        )

    evidence = payload.evidence.strip()
    comments = payload.comments.strip()

    if len(evidence) < 20 or len(comments) < 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Detailed evidence and review comments are required.",
        )

    resolved_at = datetime.now(timezone.utc)

    db.add(
        InfrastructureOperationEvent(
            operation_id=operation.id,
            actor_id=reviewer.id,
            actor_type="user",
            event_type="recovery_review_resolved",
            previous_status=operation.status,
            new_status="resolved",
            comments=comments,
            event_metadata={
                "source": "recovery_review_api",
                "decision": payload.decision,
                "verification_scope": payload.verification_scope,
                "evidence": evidence,
                "reviewer_id": reviewer.id,
                "resolved_at": resolved_at.isoformat(),
            },
        )
    )

    db.commit()

    return {
        "operation_id": operation.id,
        "status": operation.status,
        "recovery_review_status": "resolved",
        "reviewer_id": reviewer.id,
        "resolved_at": resolved_at,
    }

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

@router.post(
    "/requests/{infrastructure_request_id}/inventory/sync",
    response_model=InfrastructureInventorySyncResponse,
)
def synchronize_infrastructure_inventory(
    infrastructure_request_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Synchronize resource inventory from the current Terraform state."""

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

        state = read_state_object(
            client,
            state_record.state_bucket,
            state_record.state_key,
        )

        metadata = parse_terraform_state(state["content"])

        result = sync_infrastructure_inventory(
            db,
            infrastructure_request_id,
            metadata["serial"],
            metadata["resources"],
            state_version_id=state.get("version_id"),
            state_lineage=metadata["lineage"],
            state_sha256=state.get("sha256"),
        )

    except S3StateStorageError as exc:
        raise HTTPException(
            status_code=502,
            detail="Terraform state retrieval or validation failed.",
        ) from exc

    except InfrastructureInventoryError as exc:
        raise HTTPException(
            status_code=409,
            detail="Infrastructure inventory synchronization failed.",
        ) from exc

    return {
        "infrastructure_request_id": infrastructure_request_id,
        **result,
    }

@router.get(
    "/requests/{infrastructure_request_id}/health",
)
def get_infrastructure_resource_health(
    infrastructure_request_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Retrieve live health information for inventoried EC2 instances."""

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

    if state_record.status == "archived":
        raise HTTPException(
            status_code=409,
            detail="Archived Terraform state cannot be monitored.",
        )

    resources = db.scalars(
        select(InfrastructureResource).where(
            InfrastructureResource.infrastructure_request_id
            == infrastructure_request_id,
            InfrastructureResource.resource_type == "aws_instance",
            InfrastructureResource.mode == "managed",
            InfrastructureResource.is_present.is_(True),
        )
    ).all()

    instance_ids = [
        resource.resource_id
        for resource in resources
        if resource.resource_id
    ]

    if not instance_ids:
        return {
            "infrastructure_request_id": infrastructure_request_id,
            "total_instances": 0,
            "health": [],
        }

    try:
        ec2_client = create_ec2_client(state_record.state_region)

        health = get_ec2_resource_health(
            ec2_client,
            instance_ids,
        )

    except ResourceHealthError as exc:
        raise HTTPException(
            status_code=502,
            detail="EC2 resource health retrieval failed.",
        ) from exc

    return {
        "infrastructure_request_id": infrastructure_request_id,
        "total_instances": len(health),
        "health": health,
    }
