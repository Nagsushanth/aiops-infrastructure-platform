
"""Background tasks for infrastructure lifecycle operations."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import subprocess
from pathlib import Path
from sqlalchemy import select, update

from backend.app.core.database import SessionLocal
from backend.app.models.approval import (
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_operation import (
    InfrastructureOperation,
)
from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)
from backend.app.models.infrastructure_request import (
    InfrastructureRequest,
)
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    load_plan_artifact,
    load_provider_lock_artifact,
)
from backend.app.workers.celery_app import celery_app
from backend.app.services.terraform.execution import (
    _run_controlled_process,
)


WORKER_LEASE_MINUTES = 15


@celery_app.task(name="aiops.health_check")
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "aiops-celery-worker",
    }


def _fingerprint_specification(specification) -> str:
    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()

def _renew_operation_lease(
    operation_id: int,
    execution_token: str,
    lease_minutes: int = WORKER_LEASE_MINUTES,
) -> bool:
    """Renew a lease only while this worker still owns a running operation."""

    if (
        not isinstance(lease_minutes, int)
        or isinstance(lease_minutes, bool)
        or lease_minutes <= 0
    ):
        raise ValueError("lease_minutes must be a positive integer.")

    db = SessionLocal()

    try:
        now = datetime.now(timezone.utc)

        result = db.execute(
            update(InfrastructureOperation)
            .where(
                InfrastructureOperation.id == operation_id,
                InfrastructureOperation.status == "running",
                InfrastructureOperation.execution_token == execution_token,
                InfrastructureOperation.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + timedelta(minutes=lease_minutes),
            )
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            db.rollback()
            return False

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

def _run_owned_operation_process(
    command: list[str],
    *,
    operation_id: int,
    execution_token: str,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
    lease_renewal_interval_seconds: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run a process while renewing the owning infrastructure operation."""

    return _run_controlled_process(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        env=env,
        lease_renewal_callback=lambda: _renew_operation_lease(
            operation_id,
            execution_token,
        ),
        lease_renewal_interval_seconds=lease_renewal_interval_seconds,
    )


def _finish_operation(
    db,
    operation_id: int,
    execution_token: str,
    status: str,
    error_code: str,
    message: str,
    preflight_passed: bool = False,
) -> bool:
    """Finish an operation only while this worker owns its lease."""

    now = datetime.now(timezone.utc)

    result = db.execute(
        update(InfrastructureOperation)
        .where(
            InfrastructureOperation.id == operation_id,
            InfrastructureOperation.status == "running",
            InfrastructureOperation.execution_token == execution_token,
            InfrastructureOperation.lease_expires_at > now,
        )
        .values(
            status=status,
            error_code=error_code,
            completed_at=now,
            execution_token=None,
            lease_expires_at=None,
            result_summary={
                "message": message,
                "preflight_passed": preflight_passed,
                "terraform_apply_enabled": False,
            },
        )
        .execution_options(synchronize_session=False)
    )

    if result.rowcount != 1:
        db.rollback()
        return False

    db.add(
        InfrastructureOperationEvent(
            operation_id=operation_id,
            actor_id=None,
            actor_type="system",
            event_type="worker_operation_finished",
            previous_status="running",
            new_status=status,
            comments=message,
            event_metadata={
                "source": "celery_worker",
                "error_code": error_code,
                "preflight_passed": preflight_passed,
            },
        )
    )

    db.commit()
    return True


def _owned_operation_result(
    db,
    operation_id: int,
    execution_token: str,
    status: str,
    error_code: str,
    message: str,
    preflight_passed: bool = False,
) -> dict:
    finished = _finish_operation(
        db=db,
        operation_id=operation_id,
        execution_token=execution_token,
        status=status,
        error_code=error_code,
        message=message,
        preflight_passed=preflight_passed,
    )

    if not finished:
        db.expire_all()
        operation = db.get(InfrastructureOperation, operation_id)

        return {
            "status": operation.status if operation else "not_found",
            "operation_id": operation_id,
            "message": "Worker no longer owns this operation.",
        }

    return {
        "status": status,
        "operation_id": operation_id,
        "error_code": error_code,
        "preflight_passed": preflight_passed,
        "terraform_apply_enabled": False,
    }


@celery_app.task(
    name="aiops.execute_provision_operation",
)
def execute_provision_operation(operation_id: int) -> dict:
    """Revalidate a provision request without executing Terraform apply."""

    db = SessionLocal()
    execution_token = str(uuid4())

    try:
        now = datetime.now(timezone.utc)

        # Atomically claim the operation.
        claim_result = db.execute(
            update(InfrastructureOperation)
            .where(
                InfrastructureOperation.id == operation_id,
                InfrastructureOperation.status == "queued",
            )
            .values(
                status="running",
                started_at=now,
                execution_token=execution_token,
                lease_expires_at=now + timedelta(
                    minutes=WORKER_LEASE_MINUTES
                ),
            )
        )

        if claim_result.rowcount != 1:
            db.rollback()

            operation = db.get(
                InfrastructureOperation,
                operation_id,
            )

            if operation is None:
                return {
                    "status": "not_found",
                    "operation_id": operation_id,
                }

            return {
                "status": operation.status,
                "operation_id": operation_id,
                "message": "Operation is not queued.",
            }

        db.commit()
        db.expire_all()

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        if operation is None:
            return {
                "status": "not_found",
                "operation_id": operation_id,
            }

        approval = db.get(
            ApprovalRequest,
            operation.approval_request_id,
        )

        plan = db.get(
            TerraformPlanRecord,
            operation.terraform_plan_record_id,
        )

        infrastructure_request = db.get(
            InfrastructureRequest,
            operation.infrastructure_request_id,
        )

        if (
            operation.operation != "provision"
            or approval is None
            or plan is None
            or infrastructure_request is None
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "OPERATION_RELATIONSHIP_INVALID",
                "The operation references missing or invalid records.",
            )

        if (
            approval.requester_id != operation.requester_id
            or approval.infrastructure_request_id
            != infrastructure_request.id
            or approval.terraform_plan_record_id != plan.id
            or plan.infrastructure_request_id != infrastructure_request.id
            or plan.requester_id != operation.requester_id
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "OPERATION_OWNERSHIP_MISMATCH",
                "The operation ownership or record relationships are invalid.",
            )

        conversation = db.get(
            Conversation,
            infrastructure_request.conversation_id,
        )

        if (
            conversation is None
            or conversation.user_id != operation.requester_id
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "REQUEST_OWNERSHIP_MISMATCH",
                "The infrastructure request ownership check failed.",
            )

        if approval.status != "approved":
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "APPROVAL_NOT_FINAL",
                "The approval is no longer finally approved.",
            )

        if plan.status != "succeeded":
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_NOT_SUCCEEDED",
                "The Terraform plan is not in succeeded status.",
            )

        if (
            not plan.plan_fingerprint
            or approval.plan_fingerprint != plan.plan_fingerprint
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_FINGERPRINT_MISMATCH",
                "The approved plan fingerprint does not match.",
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

        required_stages = {"technical", "cost", "security"}

        if (
            set(stage_statuses) != required_stages
            or any(
                stage_statuses.get(stage) != "approved"
                for stage in required_stages
            )
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "APPROVAL_STAGES_INCOMPLETE",
                "All technical, cost and security reviews must be approved.",
            )

        try:
            specification = (
                ValidatedInfrastructureSpecification.model_validate(
                    infrastructure_request.requirements
                )
            )
        except Exception:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "SPECIFICATION_INVALID",
                "The infrastructure specification is no longer valid.",
            )

        current_fingerprint = _fingerprint_specification(specification)

        if plan.specification_fingerprint != current_fingerprint:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "SPECIFICATION_FINGERPRINT_MISMATCH",
                "Infrastructure requirements changed after plan generation.",
            )

        if not plan.artifact_path or not plan.artifact_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_ARTIFACT_MISSING",
                "The Terraform plan artifact is unavailable.",
            )

        if plan.plan_fingerprint != plan.artifact_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_ARTIFACT_FINGERPRINT_MISMATCH",
                (
                    "The approved plan fingerprint does not match "
                    "the stored artifact checksum."
                ),
            )

        try:
            load_plan_artifact(
                plan.artifact_path,
                plan.artifact_sha256,
            )
        except TerraformArtifactError:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_ARTIFACT_INVALID",
                "Terraform plan artifact integrity verification failed.",
            )


        if not plan.provider_lock_path or not plan.provider_lock_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PROVIDER_LOCK_ARTIFACT_MISSING",
                "The Terraform provider lock artifact is unavailable.",
            )

        try:
            load_provider_lock_artifact(
                plan.provider_lock_path,
                plan.provider_lock_sha256,
            )
        except TerraformArtifactError:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PROVIDER_LOCK_ARTIFACT_INVALID",
                "Terraform provider lock artifact integrity verification failed.",
            )


        # Deliberate safety boundary:
        # Terraform apply remains disabled.
        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "blocked",
            "TERRAFORM_APPLY_DISABLED",
            (
                "Preflight validation succeeded. Terraform apply remains "
                "disabled until persistent state and deployment safeguards "
                "are implemented."
            ),
            preflight_passed=True,
        )

    except Exception:
        db.rollback()

        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "failed",
            "WORKER_PREFLIGHT_FAILED",
            "An unexpected error occurred during operation preflight.",
        )

    finally:
        db.close()


@celery_app.task(
    name="aiops.recover_stale_infrastructure_operations",
)
def recover_stale_infrastructure_operations(
    grace_period_minutes: int = 0,
) -> dict:
    """Fail expired worker leases without retrying infrastructure actions."""

    if (
        isinstance(grace_period_minutes, bool)
        or not isinstance(grace_period_minutes, int)
        or not 0 <= grace_period_minutes <= 1440
    ):
        raise ValueError(
            "grace_period_minutes must be an integer between 0 and 1440."
        )

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    expiry_cutoff = now - timedelta(
        minutes=grace_period_minutes
    )

    recovered_ids = []

    try:
        candidate_ids = list(
            db.scalars(
                select(InfrastructureOperation.id).where(
                    InfrastructureOperation.status == "running",
                    InfrastructureOperation.execution_token.is_not(None),
                    InfrastructureOperation.lease_expires_at.is_not(None),
                    InfrastructureOperation.lease_expires_at <= expiry_cutoff,
                )
            ).all()
        )

        for operation_id in candidate_ids:
            result = db.execute(
                update(InfrastructureOperation)
                .where(
                    InfrastructureOperation.id == operation_id,
                    InfrastructureOperation.status == "running",
                    InfrastructureOperation.execution_token.is_not(None),
                    InfrastructureOperation.lease_expires_at <= expiry_cutoff,
                )
                .values(
                    status="failed",
                    error_code="WORKER_STALE_OPERATION",
                    completed_at=now,
                    execution_token=None,
                    lease_expires_at=None,
                    result_summary={
                        "message": (
                            "The worker lease expired. The operation was "
                            "marked failed for manual review. No automatic "
                            "infrastructure retry was performed."
                        ),
                        "recovery_source": "celery_recovery_task",
                        "terraform_apply_enabled": False,
                        "manual_review_required": True,
                    },
                )
            )

            if result.rowcount != 1:
                continue

            db.add(
                InfrastructureOperationEvent(
                    operation_id=operation_id,
                    actor_id=None,
                    actor_type="system",
                    event_type="worker_lease_expired",
                    previous_status="running",
                    new_status="failed",
                    comments=(
                        "Worker lease expired. Operation requires manual review."
                    ),
                    event_metadata={
                        "source": "celery_recovery_task",
                        "error_code": "WORKER_STALE_OPERATION",
                        "automatic_retry": False,
                    },
                )
            )

            recovered_ids.append(operation_id)

        db.commit()

        return {
            "status": "completed",
            "recovered_count": len(recovered_ids),
            "recovered_operation_ids": recovered_ids,
            "automatic_retry": False,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
