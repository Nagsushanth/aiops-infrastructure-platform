
"""Background tasks for infrastructure lifecycle operations."""
import os
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
    load_configuration_artifact,
)
from backend.app.workers.celery_app import celery_app
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
    _run_controlled_process,
    build_terraform_environment,
    reconstructed_terraform_workspace,
    initialize_terraform_apply_workspace,
    execute_terraform_saved_plan,
    execute_terraform_compensating_plan,
)
from backend.app.services.terraform.state_manager import (
    TerraformStateError,
    get_state_record,
    verify_active_state_snapshot,
)
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    create_s3_client,
    read_state_object,
    parse_terraform_state,
)
from backend.app.services.terraform.state_manager import (
    TerraformStateError,
    get_state_record,
    verify_active_state_snapshot,
    update_state_metadata,
)

from backend.app.services.terraform.inventory_manager import (
    sync_infrastructure_inventory,
    InfrastructureInventoryError,
)
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    create_s3_client,
)
from backend.app.models.infrastructure_resource import (
    InfrastructureResource,
)
from backend.app.models.user import User
from backend.app.services.terraform.operation_safety import (
    has_unresolved_recovery_operation,
)

WORKER_LEASE_MINUTES = 15

def _terraform_feature_enabled(name: str) -> bool:
    """Enable Terraform execution only through an explicit true flag."""
    return os.getenv(name, "").strip().lower() == "true"


def _load_last_known_good_provisioning_artifacts(
    db,
    infrastructure_request_id: int,
) -> dict:
    """Find and verify the latest successfully deployed configuration."""

    statement = (
        select(InfrastructureOperation, TerraformPlanRecord)
        .join(
            TerraformPlanRecord,
            InfrastructureOperation.terraform_plan_record_id
            == TerraformPlanRecord.id,
        )
        .where(
            InfrastructureOperation.infrastructure_request_id
            == infrastructure_request_id,
            InfrastructureOperation.operation == "provision",
            InfrastructureOperation.status == "succeeded",
        )
        .order_by(InfrastructureOperation.created_at.desc())
    )

    candidates = db.execute(statement).all()

    for operation, plan in candidates:
        summary = operation.result_summary or {}

        if not (
            summary.get("terraform_apply_executed") is True
            and summary.get("deployment_succeeded") is True
            and summary.get("state_reconciled") is True
        ):
            continue

        if (
            plan.infrastructure_request_id != infrastructure_request_id
            or plan.operation != "provision"
            or plan.status != "succeeded"
            or not plan.configuration_artifact_path
            or not plan.configuration_artifact_sha256
            or not plan.provider_lock_path
            or not plan.provider_lock_sha256
        ):
            raise TerraformExecutionError(
                "RECOVERY_BASELINE_METADATA_INVALID"
            )

        try:
            # Verify both persisted artifacts before allowing recovery
            # planning to use their references.
            load_configuration_artifact(
                plan.configuration_artifact_path,
                plan.configuration_artifact_sha256,
            )
            load_provider_lock_artifact(
                plan.provider_lock_path,
                plan.provider_lock_sha256,
            )
        except (TerraformArtifactError, OSError) as exc:
            raise TerraformExecutionError(
                "RECOVERY_BASELINE_ARTIFACT_INVALID"
            ) from exc

        return {
            "source_operation_id": operation.id,
            "source_plan_record_id": plan.id,
            "configuration_artifact_path": (
                plan.configuration_artifact_path
            ),
            "configuration_artifact_sha256": (
                plan.configuration_artifact_sha256
            ),
            "provider_lock_path": plan.provider_lock_path,
            "provider_lock_sha256": plan.provider_lock_sha256,
            "specification_fingerprint": plan.specification_fingerprint,
        }

    raise TerraformExecutionError(
        "RECOVERY_BASELINE_NOT_FOUND"
    )

def _create_recovery_approval(
    db,
    infrastructure_request_id: int,
    failed_operation_id: int,
    baseline: dict,
    result: dict,
    state_snapshot: dict,
) -> dict:
    """Persist a trusted recovery plan and its separate approval workflow."""
    required_snapshot_fields = {
        "version_id",
        "serial",
        "lineage",
        "sha256",
    }

    if (
        not isinstance(state_snapshot, dict)
        or not required_snapshot_fields.issubset(state_snapshot)
        or not isinstance(state_snapshot["version_id"], str)
        or not state_snapshot["version_id"].strip()
        or not isinstance(state_snapshot["serial"], int)
        or isinstance(state_snapshot["serial"], bool)
        or state_snapshot["serial"] < 0
        or not isinstance(state_snapshot["lineage"], str)
        or not isinstance(state_snapshot["sha256"], str)
        or len(state_snapshot["sha256"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in state_snapshot["sha256"].lower()
        )
    ):
        raise TerraformExecutionError(
            "RECOVERY_STATE_SNAPSHOT_INVALID"
        )

    from backend.app.models.approval import ApprovalAuditEvent

    infrastructure_request = db.get(
        InfrastructureRequest,
        infrastructure_request_id,
    )

    if infrastructure_request is None:
        raise TerraformExecutionError(
            "RECOVERY_REQUEST_NOT_FOUND"
        )

    conversation = db.get(
        Conversation,
        infrastructure_request.conversation_id,
    )

    if conversation is None or conversation.user_id is None:
        raise TerraformExecutionError(
            "RECOVERY_REQUESTER_NOT_FOUND"
        )

    plan_summary = result.get("plan_summary")

    if not isinstance(plan_summary, dict):
        raise TerraformExecutionError(
            "RECOVERY_PLAN_SUMMARY_INVALID"
        )

    analysis = plan_summary.get("security_analysis")

    if not isinstance(analysis, dict):
        raise TerraformExecutionError(
            "RECOVERY_SECURITY_ANALYSIS_MISSING"
        )

    expected_severities = {"critical", "high", "medium", "low"}

    if not (
        analysis.get("checks_completed") is True
        and analysis.get("deployment_enabled") is False
        and analysis.get("passed") is True
        and type(analysis.get("finding_count")) is int
        and analysis["finding_count"] == 0
        and analysis.get("findings") == []
        and isinstance(analysis.get("severity_counts"), dict)
        and set(analysis["severity_counts"]) == expected_severities
        and all(
            type(analysis["severity_counts"][key]) is int
            and analysis["severity_counts"][key] == 0
            for key in expected_severities
        )
    ):
        raise TerraformExecutionError(
            "RECOVERY_SECURITY_CHECK_FAILED"
        )

    artifact_path = result.get("artifact_path")
    artifact_sha256 = result.get("artifact_sha256")
    plan_fingerprint = result.get("plan_fingerprint")

    if (
        not artifact_path
        or not isinstance(artifact_sha256, str)
        or len(artifact_sha256) != 64
        or artifact_sha256 != plan_fingerprint
    ):
        raise TerraformExecutionError(
            "RECOVERY_PLAN_ARTIFACT_INVALID"
        )

    try:
        load_plan_artifact(artifact_path, artifact_sha256)
    except (TerraformArtifactError, OSError) as exc:
        raise TerraformExecutionError(
            "RECOVERY_PLAN_ARTIFACT_INVALID"
        ) from exc

    specification_fingerprint = baseline.get(
        "specification_fingerprint"
    )

    if (
        not isinstance(specification_fingerprint, str)
        or len(specification_fingerprint) != 64
    ):
        raise TerraformExecutionError(
            "RECOVERY_SPECIFICATION_FINGERPRINT_INVALID"
        )

    # Prevent duplicate recovery approvals if a worker retry reaches
    # this point after the original approval was committed.
    existing = db.scalar(
        select(ApprovalRequest).join(
            TerraformPlanRecord,
            ApprovalRequest.terraform_plan_record_id
            == TerraformPlanRecord.id,
        ).where(
            ApprovalRequest.infrastructure_request_id
            == infrastructure_request_id,
            TerraformPlanRecord.operation == "recovery",
            TerraformPlanRecord.plan_fingerprint == plan_fingerprint,
        )
    )

    if existing is not None:
        existing_plan = db.get(
            TerraformPlanRecord,
            existing.terraform_plan_record_id,
        )

        if existing_plan is None:
            raise TerraformExecutionError(
                "RECOVERY_EXISTING_PLAN_NOT_FOUND"
            )

        existing_snapshot = {
            "version_id": existing_plan.state_version_id,
            "serial": existing_plan.state_serial,
            "lineage": existing_plan.state_lineage,
            "sha256": existing_plan.state_sha256,
        }

        if existing_snapshot != state_snapshot:
            raise TerraformExecutionError(
                "RECOVERY_APPROVAL_STATE_SNAPSHOT_CONFLICT"
            )

        return {
            "recovery_plan_record_id": (
                existing.terraform_plan_record_id
            ),
            "recovery_approval_id": existing.id,
            "recovery_approval_status": existing.status,
        }

    plan_record = TerraformPlanRecord(
        infrastructure_request_id=infrastructure_request_id,
        requester_id=conversation.user_id,
        operation="recovery",
        status="succeeded",
        plan_fingerprint=plan_fingerprint,
        specification_fingerprint=specification_fingerprint,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        provider_lock_path=baseline["provider_lock_path"],
        provider_lock_sha256=baseline["provider_lock_sha256"],
        state_version_id=state_snapshot["version_id"],
        state_serial=state_snapshot["serial"],
        state_lineage=state_snapshot["lineage"],
        state_sha256=state_snapshot["sha256"],
        configuration_artifact_path=(
            baseline["configuration_artifact_path"]
        ),
        configuration_artifact_sha256=(
            baseline["configuration_artifact_sha256"]
        ),
        plan_summary={
            **plan_summary,
            "recovery_provenance": {
                "failed_operation_id": failed_operation_id,
                "source_operation_id": baseline["source_operation_id"],
                "source_plan_record_id": baseline[
                    "source_plan_record_id"
                ],
            },
        },
    )

    try:
        db.add(plan_record)
        db.flush()

        approval = ApprovalRequest(
            infrastructure_request_id=infrastructure_request_id,
            terraform_plan_record_id=plan_record.id,
            requester_id=conversation.user_id,
            status="pending",
            plan_summary=plan_record.plan_summary,
            plan_fingerprint=plan_fingerprint,
        )

        db.add(approval)
        db.flush()

        for stage in ("technical", "cost", "security"):
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
                actor_id=conversation.user_id,
                event_type="submitted",
                previous_status=None,
                new_status="pending",
                comments=(
                    "Recovery plan generated after infrastructure "
                    "operation failure. Independent review required."
                ),
                event_metadata={
                    "source": "compensating_recovery",
                    "failed_operation_id": failed_operation_id,
                    "source_operation_id": baseline[
                        "source_operation_id"
                    ],
                    "source_plan_record_id": baseline[
                        "source_plan_record_id"
                    ],
                    "recovery_plan_record_id": plan_record.id,
                },
            )
        )

        db.commit()
        db.refresh(approval)

        return {
            "recovery_plan_record_id": plan_record.id,
            "recovery_approval_id": approval.id,
            "recovery_approval_status": approval.status,
        }

    except Exception:
        db.rollback()
        raise

def _generate_compensating_recovery_plan(
    db,
    operation_id: int,
    execution_token: str,
    infrastructure_request_id: int,
) -> dict:
    """Generate a recovery plan from the last verified deployment."""

    if not _record_operation_progress(
        db,
        operation_id,
        execution_token,
        "compensating_recovery_plan_started",
        "Generating a recovery plan from the last known-good deployment.",
    ):
        raise TerraformExecutionError("WORKER_LEASE_LOST")

    baseline = _load_last_known_good_provisioning_artifacts(
        db,
        infrastructure_request_id,
    )

    state_record = get_state_record(
        db,
        infrastructure_request_id,
    )

    if state_record is None:
        raise TerraformExecutionError(
            "RECOVERY_STATE_RECORD_NOT_FOUND"
        )

    state_client = create_s3_client(state_record.state_region)

    try:
        initial_snapshot = verify_active_state_snapshot(
            state_client,
            state_record,
        )
    except TerraformStateError as exc:
        raise TerraformExecutionError(
            "RECOVERY_STATE_SNAPSHOT_INVALID"
        ) from exc

    env, temp_root = build_terraform_environment()

    result = execute_terraform_compensating_plan(
        configuration_artifact_path=(
            baseline["configuration_artifact_path"]
        ),
        configuration_artifact_sha256=(
            baseline["configuration_artifact_sha256"]
        ),
        provider_lock_path=baseline["provider_lock_path"],
        provider_lock_sha256=baseline["provider_lock_sha256"],
        timeout_seconds=600,
        env=env,
        temp_root=temp_root,
        lease_renewal_callback=lambda: _renew_operation_lease(
            operation_id,
            execution_token,
        ),
    )

    try:
        final_snapshot = verify_active_state_snapshot(
            state_client,
            state_record,
        )
    except TerraformStateError as exc:
        raise TerraformExecutionError(
            "RECOVERY_STATE_CHANGED_DURING_PLANNING"
        ) from exc

    if final_snapshot != initial_snapshot:
        raise TerraformExecutionError(
            "RECOVERY_STATE_CHANGED_DURING_PLANNING"
        )

    if not _record_operation_progress(
        db,
        operation_id,
        execution_token,
        "compensating_recovery_plan_generated",
        "Recovery plan generated. Separate review and approval are required.",
    ):
        raise TerraformExecutionError("WORKER_LEASE_LOST")

    approval_metadata = _create_recovery_approval(
        db=db,
        infrastructure_request_id=infrastructure_request_id,
        failed_operation_id=operation_id,
        baseline=baseline,
        result=result,
        state_snapshot=final_snapshot,
    )

    return {
        "recovery_plan_generated": True,
        "recovery_execution_enabled": False,
        "automatic_rollback_executed": False,
        "recovery_status": "RECOVERY_PLAN_READY",
        "recovery_source_operation_id": baseline[
            "source_operation_id"
        ],
        "recovery_source_plan_record_id": baseline[
            "source_plan_record_id"
        ],
        "recovery_plan_fingerprint": result["plan_fingerprint"],
        "recovery_plan_artifact_path": result["artifact_path"],
        "recovery_plan_artifact_sha256": result["artifact_sha256"],
        "recovery_plan_summary": result["plan_summary"],
        **approval_metadata,
    }

def _sync_state_after_execution(
    db,
    infrastructure_request_id: int,
) -> dict:
    """Reconcile Terraform state and inventory after successful execution."""

    state_record = get_state_record(db, infrastructure_request_id)

    if state_record is None:
        raise TerraformStateError("STATE_RECORD_MISSING")

    client = create_s3_client(state_record.state_region)

    state_object = read_state_object(
        client,
        state_record.state_bucket,
        state_record.state_key,
    )

    metadata = parse_terraform_state(state_object["content"])

    inventory_result = sync_infrastructure_inventory(
        db,
        infrastructure_request_id,
        state_serial=metadata["serial"],
        resources=metadata["resources"],
        state_version_id=state_object["version_id"],
        state_lineage=metadata["lineage"],
        state_sha256=state_object["sha256"],
    )

    update_state_metadata(
        db,
        infrastructure_request_id,
        status="active",
    )

    return {
        "state_reconciled": True,
        "state_serial": metadata["serial"],
        "inventory": inventory_result,
    }

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


def _record_operation_progress(
    db,
    operation_id: int,
    execution_token: str,
    stage: str,
    message: str,
) -> bool:
    """Record progress only while this worker owns a valid operation lease."""

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
            lease_expires_at=now + timedelta(
                minutes=WORKER_LEASE_MINUTES
            )
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
            event_type="worker_progress",
            previous_status="running",
            new_status="running",
            comments=message,
            event_metadata={
                "source": "celery_worker",
                "stage": stage,
            },
        )
    )

    db.commit()
    return True

def _finish_operation(
    db,
    operation_id: int,
    execution_token: str,
    status: str,
    error_code: str,
    message: str,
    preflight_passed: bool = False,
    result_summary_extra: dict | None = None,
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
                "terraform_destroy_enabled": False,
                "terraform_recovery_enabled": False,
                "terraform_recovery_executed": False,
                **(result_summary_extra or {}),
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
    result_summary_extra: dict | None = None,
) -> dict:
    finished = _finish_operation(
        db=db,
        operation_id=operation_id,
        execution_token=execution_token,
        status=status,
        error_code=error_code,
        message=message,
        preflight_passed=preflight_passed,
        result_summary_extra=result_summary_extra,
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
        "terraform_apply_enabled": (
            result_summary_extra or {}
        ).get("terraform_apply_enabled", False),
        "terraform_destroy_enabled": (
            result_summary_extra or {}
        ).get("terraform_destroy_enabled", False),
        **(result_summary_extra or {}),
        "terraform_recovery_enabled": (
        result_summary_extra or {}
        ).get("terraform_recovery_enabled", False),
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
                InfrastructureOperation.operation == "provision",
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

        if has_unresolved_recovery_operation(
            db,
            infrastructure_request.id,
            exclude_operation_id=operation.id,
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_REVIEW_REQUIRED",
                (
                    "Provisioning blocked because an earlier operation "
                    "requires manual recovery review."
                ),
                result_summary_extra={
                    "recovery_required": True,
                    "recovery_status": "MANUAL_REVIEW_REQUIRED",
                    "terraform_apply_attempted": False,
                    "terraform_apply_executed": False,
                },
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

        # Verify the exact Terraform configuration used to generate the plan.
        if (
            not plan.configuration_artifact_path
            or not plan.configuration_artifact_sha256
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_MISSING",
                "The Terraform configuration artifact is unavailable.",
            )

        try:
            load_configuration_artifact(
                plan.configuration_artifact_path,
                plan.configuration_artifact_sha256,
            )
        except TerraformArtifactError:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_INVALID",
                "Terraform configuration artifact integrity verification failed.",
            )

        
        if not _record_operation_progress(
            db,
            operation_id,
            execution_token,
            "terraform_initialization_started",
            "Initializing the approved Terraform workspace.",
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "WORKER_LEASE_LOST",
                "Worker no longer owns this operation.",
            )

        apply_enabled = _terraform_feature_enabled(
            "AIOPS_TERRAFORM_APPLY_ENABLED"
        )

        # Reconstruct and retain the exact approved Terraform workspace.
        env, temp_root = build_terraform_environment()
        apply_executed = False

        try:
            with reconstructed_terraform_workspace(
                configuration_artifact_path=(
                    plan.configuration_artifact_path
                ),
                configuration_artifact_sha256=(
                    plan.configuration_artifact_sha256
                ),
                provider_lock_path=plan.provider_lock_path,
                provider_lock_sha256=plan.provider_lock_sha256,
                plan_artifact_path=plan.artifact_path,
                plan_artifact_sha256=plan.artifact_sha256,
                temp_root=temp_root,
            ) as workspace:
                initialize_terraform_apply_workspace(
                    workspace,
                    timeout_seconds=180,
                    env=env,
                    lease_renewal_callback=lambda: _renew_operation_lease(
                        operation_id,
                        execution_token,
                    ),
                )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "preflight_completed",
                    "Terraform initialization and provision preflight completed successfully.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this operation.",
                    )

                if not apply_enabled:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "TERRAFORM_APPLY_DISABLED",
                        (
                            "Preflight validation succeeded. Terraform apply "
                            "remains disabled because its feature flag is off."
                        ),
                        preflight_passed=True,
                        result_summary_extra={
                            "terraform_apply_enabled": False,
                            "terraform_apply_executed": False,
                        },
                    )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "terraform_apply_started",
                    "Executing the approved Terraform saved plan.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this operation.",
                        preflight_passed=True,
                        result_summary_extra={
                            "terraform_apply_enabled": True,
                            "terraform_apply_executed": False,
                        },
                    )

                execute_terraform_saved_plan(
                    workspace,
                    timeout_seconds=600,
                    env=env,
                    lease_renewal_callback=lambda: _renew_operation_lease(
                        operation_id,
                        execution_token,
                    ),
                )
                apply_executed = True
        except TerraformExecutionError as exc:
            # Never attempt recovery if worker ownership is uncertain.
            if exc.code in {
                "WORKER_LEASE_LOST",
                "WORKER_LEASE_RENEWAL_FAILED",
            }:
                return _owned_operation_result(
                    db,
                    operation_id,
                    execution_token,
                    "blocked",
                    exc.code,
                    "Terraform execution stopped after worker ownership was lost. Manual recovery review is required.",
                    preflight_passed=True,
                    result_summary_extra={
                        "terraform_apply_enabled": apply_enabled,
                        "terraform_apply_attempted": True,
                        "terraform_apply_executed": False,
                        "deployment_succeeded": False,
                        "state_reconciled": False,
                        "recovery_required": True,
                        "recovery_status": "WORKER_OWNERSHIP_UNCERTAIN",
                        "rollback_executed": False,
                    },
                )

            # Reconcile actual state before generating a recovery plan.
            reconciliation = {}
            state_reconciled = False

            try:
                reconciliation = _sync_state_after_execution(
                    db,
                    infrastructure_request.id,
                )
                state_reconciled = True
            except Exception:
                db.rollback()

            recovery_details = {}

            if state_reconciled:
                try:
                    recovery_details = (
                        _generate_compensating_recovery_plan(
                            db,
                            operation_id,
                            execution_token,
                            infrastructure_request.id,
                        )
                    )
                except TerraformExecutionError as recovery_exc:
                    if recovery_exc.code in {
                        "WORKER_LEASE_LOST",
                        "WORKER_LEASE_RENEWAL_FAILED",
                    }:
                        return _owned_operation_result(
                            db,
                            operation_id,
                            execution_token,
                            "blocked",
                            recovery_exc.code,
                            "Recovery planning stopped because worker ownership was lost.",
                            preflight_passed=True,
                            result_summary_extra={
                                "recovery_required": True,
                                "recovery_status": "WORKER_OWNERSHIP_UNCERTAIN",
                                "recovery_execution_enabled": False,
                                "rollback_executed": False,
                            },
                        )

                    recovery_details = {
                        "recovery_plan_generated": False,
                        "recovery_execution_enabled": False,
                        "recovery_status": "MANUAL_REVIEW_REQUIRED",
                        "recovery_error_code": recovery_exc.code,
                    }
                except Exception:
                    recovery_details = {
                        "recovery_plan_generated": False,
                        "recovery_execution_enabled": False,
                        "recovery_status": "MANUAL_REVIEW_REQUIRED",
                        "recovery_error_code": "RECOVERY_PLAN_GENERATION_FAILED",
                    }
            else:
                recovery_details = {
                    "recovery_plan_generated": False,
                    "recovery_execution_enabled": False,
                    "recovery_status": "MANUAL_REVIEW_REQUIRED",
                }

            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                exc.code,
                (
                    "Terraform Apply failed. A compensating recovery plan "
                    "was generated and requires separate approval."
                    if recovery_details.get("recovery_plan_generated")
                    else
                    "Terraform Apply failed. Recovery requires manual review."
                ),
                preflight_passed=True,
                result_summary_extra={
                    "terraform_apply_enabled": apply_enabled,
                    "terraform_apply_attempted": True,
                    "terraform_apply_executed": False,
                    "deployment_succeeded": False,
                    "state_reconciled": state_reconciled,
                    "recovery_required": True,
                    "rollback_executed": False,
                    **reconciliation,
                    **recovery_details,
                },
            )

        if not apply_executed:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                "TERRAFORM_APPLY_NOT_EXECUTED",
                "Terraform Apply did not complete.",
                preflight_passed=True,
                result_summary_extra={
                    "terraform_apply_enabled": apply_enabled,
                    "terraform_apply_executed": False,
                },
            )

        # Terraform succeeded. Reconcile the resulting state and inventory.
        try:
            reconciliation = _sync_state_after_execution(
                db,
                infrastructure_request.id,
            )
        except Exception:
            db.rollback()
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                "STATE_RECONCILIATION_REQUIRED",
                (
                    "Terraform Apply succeeded, but state and inventory "
                    "reconciliation requires manual review."
                ),
                preflight_passed=True,
                result_summary_extra={
                    "terraform_apply_enabled": True,
                    "terraform_apply_executed": True,
                    "deployment_succeeded": True,
                    "state_reconciled": False,
                    "state_reconciliation_required": True,
                },
            )

        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "succeeded",
            "NONE",
            "Terraform Apply completed and infrastructure inventory was synchronized.",
            preflight_passed=True,
            result_summary_extra={
                "terraform_apply_enabled": True,
                "terraform_apply_executed": True,
                "deployment_succeeded": True,
                **reconciliation,
            },
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
    name="aiops.execute_deprovision_operation",
)
def execute_deprovision_operation(operation_id: int) -> dict:
    """Validate a confirmed destroy request without executing Terraform destroy."""

    db = SessionLocal()
    execution_token = str(uuid4())

    try:
        now = datetime.now(timezone.utc)

        # Atomically claim only a queued deprovision operation.
        claim_result = db.execute(
            update(InfrastructureOperation)
            .where(
                InfrastructureOperation.id == operation_id,
                InfrastructureOperation.status == "queued",
                InfrastructureOperation.operation == "deprovision",
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
                "message": (
                    "Operation is not a queued deprovision operation."
                ),
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

        # Validate explicit destroy confirmation.
        result_summary = operation.result_summary or {}

        if result_summary.get("destroy_confirmed") is not True:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DESTROY_CONFIRMATION_MISSING",
                "Explicit destroy confirmation is missing.",
                result_summary_extra={
                    "destroy_confirmed": False,
                    "terraform_destroy_enabled": False,
                },
            )

        # Load the records associated with this operation.
        approval = db.get(
            ApprovalRequest,
            operation.approval_request_id,
        )
        infrastructure_request = db.get(
            InfrastructureRequest,
            operation.infrastructure_request_id,
        )
        plan = db.get(
            TerraformPlanRecord,
            operation.terraform_plan_record_id,
        )

        if not all((approval, infrastructure_request, plan)):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DEPROVISION_RECORDS_MISSING",
                "Required approval, infrastructure request or plan was not found.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Verify that all records refer to the same request and plan.
        if (
            approval.infrastructure_request_id
            != infrastructure_request.id
            or approval.terraform_plan_record_id != plan.id
            or plan.infrastructure_request_id
            != infrastructure_request.id
            or plan.requester_id != operation.requester_id
            or approval.requester_id != operation.requester_id
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DEPROVISION_OWNERSHIP_MISMATCH",
                "The operation, approval and Terraform plan ownership do not match.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Verify that the infrastructure request belongs to the requester.
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
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Only an approved deprovision plan can continue.
        if (
            operation.operation != "deprovision"
            or plan.operation != "deprovision"
            or plan.status != "succeeded"
            or approval.status != "approved"
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DEPROVISION_APPROVAL_INVALID",
                "The deprovision plan or its approval is not in an eligible state.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # The approval must reference this exact Terraform plan.
        if approval.plan_fingerprint != plan.plan_fingerprint:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DEPROVISION_PLAN_FINGERPRINT_MISMATCH",
                "The approved plan fingerprint does not match the deprovision plan.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Verify that all three independent approval stages are approved.
        stage_reviews = db.execute(
            select(ApprovalStageReview).where(
                ApprovalStageReview.approval_request_id == approval.id,
            )
        ).scalars().all()

        required_stages = {"technical", "cost", "security"}

        review_statuses = {
            review.stage: review.status
            for review in stage_reviews
        }

        if (
            set(review_statuses) != required_stages
            or any(
                review_statuses[stage] != "approved"
                for stage in required_stages
            )
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DEPROVISION_STAGE_APPROVAL_INCOMPLETE",
                "Technical, cost and security reviews must all be approved before destruction.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Revalidate the current infrastructure specification.
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
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        current_fingerprint = _fingerprint_specification(
            specification
        )

        if plan.specification_fingerprint != current_fingerprint:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "SPECIFICATION_FINGERPRINT_MISMATCH",
                "Infrastructure requirements changed after destroy plan generation.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Retrieve the state record for this infrastructure request.
        state_record = get_state_record(
            db,
            infrastructure_request.id,
        )

        if state_record is None:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "STATE_RECORD_MISSING",
                "Terraform state record was not found.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": False,
                },
            )

        # Verify the current S3 state against the inventory snapshot.
        try:
            s3_client = create_s3_client(
                state_record.state_region,
            )

            current_snapshot = verify_active_state_snapshot(
                s3_client,
                state_record,
            )

        except (TerraformStateError, S3StateStorageError):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "STATE_SNAPSHOT_VERIFICATION_FAILED",
                "The current Terraform state could not be verified. Synchronize inventory and investigate before destruction.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": False,
                },
            )

        # The destroy plan must reference exactly the same state
        # version, serial, lineage and checksum that we just verified.
        plan_snapshot = {
            "version_id": plan.state_version_id,
            "serial": plan.state_serial,
            "lineage": plan.state_lineage,
            "sha256": plan.state_sha256,
        }

        if plan_snapshot != current_snapshot:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "DESTROY_PLAN_STATE_SNAPSHOT_MISMATCH",
                "Terraform state has changed since the destroy plan was generated. Generate a new destroy plan before proceeding.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": False,
                },
            )

        if not _record_operation_progress(
            db,
            operation_id,
            execution_token,
            "state_snapshot_verified",
            "Current Terraform state matches the approved destroy plan snapshot.",
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "WORKER_LEASE_LOST",
                "Worker no longer owns this operation.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                },
            )

        # Verify the Terraform binary plan artifact.
        if not plan.artifact_path or not plan.artifact_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_ARTIFACT_MISSING",
                "The Terraform destroy plan artifact is unavailable.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )

        if plan.plan_fingerprint != plan.artifact_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PLAN_ARTIFACT_FINGERPRINT_MISMATCH",
                "The destroy plan fingerprint does not match its stored artifact checksum.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
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
                "Terraform destroy plan artifact integrity verification failed.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )

        # Verify the provider lock artifact.
        if not plan.provider_lock_path or not plan.provider_lock_sha256:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PROVIDER_LOCK_ARTIFACT_MISSING",
                "The Terraform provider lock artifact is unavailable.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
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
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )

        # Verify the exact Terraform configuration used by the destroy plan.
        if (
            not plan.configuration_artifact_path
            or not plan.configuration_artifact_sha256
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_MISSING",
                "The Terraform configuration artifact is unavailable.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )

        try:
            load_configuration_artifact(
                plan.configuration_artifact_path,
                plan.configuration_artifact_sha256,
            )
        except TerraformArtifactError:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_INVALID",
                "Terraform configuration artifact integrity verification failed.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )

        if not _record_operation_progress(
            db,
            operation_id,
            execution_token,
            "terraform_initialization_started",
            "Initializing the approved Terraform destroy workspace.",
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "WORKER_LEASE_LOST",
                "Worker no longer owns this operation.",
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": False,
                    "state_snapshot_verified": True,
                },
            )
        destroy_enabled = _terraform_feature_enabled(
            "AIOPS_TERRAFORM_DESTROY_ENABLED"
        )
        # Reconstruct and retain the exact approved destroy workspace.
        env, temp_root = build_terraform_environment()
        destroy_executed = False
        try:
            with reconstructed_terraform_workspace(
                configuration_artifact_path=(
                    plan.configuration_artifact_path
                ),
                configuration_artifact_sha256=(
                    plan.configuration_artifact_sha256
                ),
                provider_lock_path=plan.provider_lock_path,
                provider_lock_sha256=plan.provider_lock_sha256,
                plan_artifact_path=plan.artifact_path,
                plan_artifact_sha256=plan.artifact_sha256,
                temp_root=temp_root,
            ) as workspace:
                try:
                    initialize_terraform_apply_workspace(
                        workspace,
                        timeout_seconds=180,
                        env=env,
                        lease_renewal_callback=lambda: _renew_operation_lease(
                            operation_id,
                            execution_token,
                        ),
                    )
                except TerraformExecutionError:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "failed",
                        "TERRAFORM_INITIALIZATION_FAILED",
                        "Terraform workspace initialization failed during destroy preflight.",
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": destroy_enabled,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": True,
                            "terraform_initialization_completed": False,
                        },
                    )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "preflight_completed",
                    "Terraform destroy preflight and initialization completed successfully.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this operation.",
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": destroy_enabled,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": True,
                            "terraform_initialization_completed": True,
                        },
                    )

                if not destroy_enabled:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "TERRAFORM_DESTROY_DISABLED",
                        (
                            "Destroy preflight completed successfully. "
                            "Terraform destroy execution remains disabled "
                            "because its feature flag is off."
                        ),
                        preflight_passed=True,
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": False,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": True,
                            "terraform_initialization_completed": True,
                        },
                    )

                # Reverify the live state immediately before destruction.
                try:
                    latest_s3_client = create_s3_client(
                        state_record.state_region
                    )
                    latest_snapshot = verify_active_state_snapshot(
                        latest_s3_client,
                        state_record,
                    )
                except (TerraformStateError, S3StateStorageError):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "STATE_SNAPSHOT_VERIFICATION_FAILED",
                        (
                            "Terraform state could not be reverified "
                            "immediately before destruction."
                        ),
                        preflight_passed=True,
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": True,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": False,
                            "terraform_initialization_completed": True,
                        },
                    )

                if latest_snapshot != plan_snapshot:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "DESTROY_PLAN_STATE_SNAPSHOT_MISMATCH",
                        (
                            "Terraform state changed after preflight. "
                            "Generate and approve a new destroy plan."
                        ),
                        preflight_passed=True,
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": True,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": False,
                            "terraform_initialization_completed": True,
                        },
                    )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "terraform_destroy_started",
                    "Executing the approved Terraform saved destroy plan.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this operation.",
                        preflight_passed=True,
                        result_summary_extra={
                            "destroy_confirmed": True,
                            "terraform_destroy_enabled": True,
                            "terraform_destroy_executed": False,
                            "state_snapshot_verified": True,
                        },
                    )

                # Execute only the previously approved binary destroy plan.
                execute_terraform_saved_plan(
                    workspace,
                    timeout_seconds=600,
                    env=env,
                    lease_renewal_callback=lambda: _renew_operation_lease(
                        operation_id,
                        execution_token,
                    ),
                )
                destroy_executed = True

        except TerraformExecutionError as exc:
            # Never attempt recovery if worker ownership is uncertain.
            if exc.code in {
                "WORKER_LEASE_LOST",
                "WORKER_LEASE_RENEWAL_FAILED",
            }:
                return _owned_operation_result(
                    db,
                    operation_id,
                    execution_token,
                    "blocked",
                    exc.code,
                    (
                        "Terraform Destroy stopped after worker ownership "
                        "was lost. Manual recovery review is required."
                    ),
                    preflight_passed=True,
                    result_summary_extra={
                        "destroy_confirmed": True,
                        "terraform_destroy_enabled": destroy_enabled,
                        "terraform_destroy_attempted": True,
                        "terraform_destroy_executed": False,
                        "deployment_succeeded": False,
                        "state_reconciled": False,
                        "recovery_required": True,
                        "recovery_status": "WORKER_OWNERSHIP_UNCERTAIN",
                        "rollback_executed": False,
                    },
                )

            # A failed Destroy may have partially removed infrastructure.
            # Reconcile actual state before generating a recovery plan.
            reconciliation = {}
            state_reconciled = False

            try:
                reconciliation = _sync_state_after_execution(
                    db,
                    infrastructure_request.id,
                )
                state_reconciled = True
            except Exception:
                db.rollback()

            recovery_details = {}

            if state_reconciled:
                try:
                    recovery_details = (
                        _generate_compensating_recovery_plan(
                            db,
                            operation_id,
                            execution_token,
                            infrastructure_request.id,
                        )
                    )
                except TerraformExecutionError as recovery_exc:
                    if recovery_exc.code in {
                        "WORKER_LEASE_LOST",
                        "WORKER_LEASE_RENEWAL_FAILED",
                    }:
                        return _owned_operation_result(
                            db,
                            operation_id,
                            execution_token,
                            "blocked",
                            recovery_exc.code,
                            "Recovery planning stopped because worker ownership was lost.",
                            preflight_passed=True,
                            result_summary_extra={
                                "recovery_required": True,
                                "recovery_status": "WORKER_OWNERSHIP_UNCERTAIN",
                                "recovery_execution_enabled": False,
                                "rollback_executed": False,
                            },
                        )

                    recovery_details = {
                        "recovery_plan_generated": False,
                        "recovery_execution_enabled": False,
                        "recovery_status": "MANUAL_REVIEW_REQUIRED",
                        "recovery_error_code": recovery_exc.code,
                    }
                except Exception:
                    recovery_details = {
                        "recovery_plan_generated": False,
                        "recovery_execution_enabled": False,
                        "recovery_status": "MANUAL_REVIEW_REQUIRED",
                        "recovery_error_code": "RECOVERY_PLAN_GENERATION_FAILED",
                    }
            else:
                recovery_details = {
                    "recovery_plan_generated": False,
                    "recovery_execution_enabled": False,
                    "recovery_status": "MANUAL_REVIEW_REQUIRED",
                }

            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                exc.code,
                (
                    "Terraform Destroy failed. A compensating recovery plan "
                    "was generated and requires separate approval."
                    if recovery_details.get("recovery_plan_generated")
                    else
                    "Terraform Destroy failed. Recovery requires manual review."
                ),
                preflight_passed=True,
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": destroy_enabled,
                    "terraform_destroy_attempted": True,
                    "terraform_destroy_executed": False,
                    "deployment_succeeded": False,
                    "state_reconciled": state_reconciled,
                    "recovery_required": True,
                    "rollback_executed": False,
                    **reconciliation,
                    **recovery_details,
                },
            )

        if not destroy_executed:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                "TERRAFORM_DESTROY_NOT_EXECUTED",
                "Terraform Destroy did not complete.",
                preflight_passed=True,
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": destroy_enabled,
                    "terraform_destroy_executed": False,
                },
            )

        # Terraform succeeded. Reconcile resulting state and inventory.
        try:
            reconciliation = _sync_state_after_execution(
                db,
                infrastructure_request.id,
            )
        except Exception:
            db.rollback()
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                "STATE_RECONCILIATION_REQUIRED",
                (
                    "Terraform Destroy succeeded, but state and inventory "
                    "reconciliation requires manual review."
                ),
                preflight_passed=True,
                result_summary_extra={
                    "destroy_confirmed": True,
                    "terraform_destroy_enabled": True,
                    "terraform_destroy_executed": True,
                    "deployment_succeeded": True,
                    "state_reconciled": False,
                    "state_reconciliation_required": True,
                },
            )

        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "succeeded",
            "NONE",
            (
                "Terraform Destroy completed and infrastructure "
                "inventory was synchronized."
            ),
            preflight_passed=True,
            result_summary_extra={
                "destroy_confirmed": True,
                "terraform_destroy_enabled": True,
                "terraform_destroy_executed": True,
                "deployment_succeeded": True,
                **reconciliation,
            },
        )

    except Exception:
        db.rollback()

        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "failed",
            "DEPROVISION_WORKER_FAILED",
            "An unexpected error occurred during deprovision preflight.",
            result_summary_extra={
                "terraform_destroy_enabled": False,
            },
        )

    finally:
        db.close()


@celery_app.task(
    name="aiops.execute_recovery_operation",
)
def execute_recovery_operation(operation_id: int) -> dict:
    """Execute an independently approved compensating recovery plan."""

    db = SessionLocal()
    execution_token = str(uuid4())
    recovery_enabled = _terraform_feature_enabled(
        "AIOPS_TERRAFORM_RECOVERY_ENABLED"
    )

    try:
        now = datetime.now(timezone.utc)

        # Atomically claim only a queued recovery operation.
        claim_result = db.execute(
            update(InfrastructureOperation)
            .where(
                InfrastructureOperation.id == operation_id,
                InfrastructureOperation.status == "queued",
                InfrastructureOperation.operation == "recovery",
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
                "message": (
                    "Operation is not a queued recovery operation."
                ),
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
        infrastructure_request = db.get(
            InfrastructureRequest,
            operation.infrastructure_request_id,
        )
        plan = db.get(
            TerraformPlanRecord,
            operation.terraform_plan_record_id,
        )

        if not all((approval, infrastructure_request, plan)):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_RECORDS_MISSING",
                "Required recovery approval, request or plan was not found.",
            )

        # Verify that all related records belong to the same request.
        if (
            operation.operation != "recovery"
            or plan.operation != "recovery"
            or plan.status != "succeeded"
            or approval.infrastructure_request_id
            != infrastructure_request.id
            or approval.terraform_plan_record_id != plan.id
            or plan.infrastructure_request_id
            != infrastructure_request.id
            or plan.requester_id != operation.requester_id
            or approval.requester_id != operation.requester_id
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_OWNERSHIP_MISMATCH",
                "Recovery operation, approval, request and plan do not match.",
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

        # Require final approval and all three independent reviews.
        if approval.status != "approved":
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_APPROVAL_NOT_APPROVED",
                "The recovery plan has not received final approval.",
            )

        reviews = db.execute(
            select(ApprovalStageReview).where(
                ApprovalStageReview.approval_request_id
                == approval.id
            )
        ).scalars().all()

        required_stages = {
            "technical",
            "cost",
            "security",
        }

        review_statuses = {
            review.stage: review.status
            for review in reviews
        }

        if (
            set(review_statuses) != required_stages
            or any(
                review_statuses[stage] != "approved"
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

        # Ensure infrastructure requirements have not changed.
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

        current_fingerprint = _fingerprint_specification(
            specification
        )

        if plan.specification_fingerprint != current_fingerprint:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "SPECIFICATION_FINGERPRINT_MISMATCH",
                "Infrastructure requirements changed after recovery planning.",
            )

        # Verify the recovery plan fingerprint and binary artifact.
        if (
            not plan.artifact_path
            or not plan.artifact_sha256
            or not plan.plan_fingerprint
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_PLAN_ARTIFACT_MISSING",
                "The approved recovery plan artifact is unavailable.",
                result_summary_extra={
                    "terraform_recovery_enabled": recovery_enabled,
                    "terraform_recovery_executed": False,
                },
            )

        if (
            plan.plan_fingerprint != plan.artifact_sha256
            or approval.plan_fingerprint != plan.plan_fingerprint
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_PLAN_FINGERPRINT_MISMATCH",
                "The recovery plan fingerprint does not match its approval.",
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
                "RECOVERY_PLAN_ARTIFACT_INVALID",
                "Recovery plan artifact integrity verification failed.",
            )

        # Verify the provider lock artifact.
        if (
            not plan.provider_lock_path
            or not plan.provider_lock_sha256
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "PROVIDER_LOCK_ARTIFACT_MISSING",
                "The recovery provider lock artifact is unavailable.",
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
                "Recovery provider lock integrity verification failed.",
            )

        # Verify the exact configuration used to generate the plan.
        if (
            not plan.configuration_artifact_path
            or not plan.configuration_artifact_sha256
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_MISSING",
                "The recovery configuration artifact is unavailable.",
            )

        try:
            load_configuration_artifact(
                plan.configuration_artifact_path,
                plan.configuration_artifact_sha256,
            )
        except TerraformArtifactError:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "CONFIGURATION_ARTIFACT_INVALID",
                "Recovery configuration integrity verification failed.",
            )

        # Verify the exact Terraform state snapshot approved for recovery.
        state_record = get_state_record(
            db,
            infrastructure_request.id,
        )

        if state_record is None:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "STATE_RECORD_MISSING",
                "Terraform state record is unavailable for recovery.",
            )

        plan_snapshot = {
            "version_id": plan.state_version_id,
            "serial": plan.state_serial,
            "lineage": plan.state_lineage,
            "sha256": plan.state_sha256,
        }

        if (
            not plan_snapshot["version_id"]
            or not isinstance(plan_snapshot["serial"], int)
            or not plan_snapshot["lineage"]
            or not plan_snapshot["sha256"]
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_STATE_SNAPSHOT_INVALID",
                "The approved recovery state snapshot is incomplete.",
            )

        try:
            s3_client = create_s3_client(state_record.state_region)
            current_snapshot = verify_active_state_snapshot(
                s3_client,
                state_record,
            )
        except (TerraformStateError, S3StateStorageError):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "STATE_SNAPSHOT_VERIFICATION_FAILED",
                "Current Terraform state could not be verified.",
            )

        if current_snapshot != plan_snapshot:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "RECOVERY_STATE_SNAPSHOT_MISMATCH",
                "Terraform state changed after recovery approval. A new recovery plan is required.",
            )

        if not _record_operation_progress(
            db,
            operation_id,
            execution_token,
            "recovery_state_snapshot_verified",
            "Current Terraform state matches the approved recovery snapshot.",
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "WORKER_LEASE_LOST",
                "Worker no longer owns this recovery operation.",
            )

        if not _record_operation_progress(
            db,
            operation_id,
            execution_token,
            "terraform_initialization_started",
            "Initializing the approved Terraform recovery workspace.",
        ):
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "blocked",
                "WORKER_LEASE_LOST",
                "Worker no longer owns this recovery operation.",
            )

        env, temp_root = build_terraform_environment()
        recovery_executed = False

        try:
            with reconstructed_terraform_workspace(
                configuration_artifact_path=(
                    plan.configuration_artifact_path
                ),
                configuration_artifact_sha256=(
                    plan.configuration_artifact_sha256
                ),
                provider_lock_path=plan.provider_lock_path,
                provider_lock_sha256=plan.provider_lock_sha256,
                plan_artifact_path=plan.artifact_path,
                plan_artifact_sha256=plan.artifact_sha256,
                temp_root=temp_root,
            ) as workspace:

                try:
                    initialize_terraform_apply_workspace(
                        workspace,
                        timeout_seconds=180,
                        env=env,
                        lease_renewal_callback=lambda: (
                            _renew_operation_lease(
                                operation_id,
                                execution_token,
                            )
                        ),
                    )
                except TerraformExecutionError as exc:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "failed",
                        "RECOVERY_INITIALIZATION_FAILED",
                        "Terraform recovery workspace initialization failed.",
                        result_summary_extra={
                            "terraform_recovery_enabled": recovery_enabled,
                            "terraform_recovery_executed": False,
                            "recovery_error_code": exc.code,
                            "state_snapshot_verified": True,
                        },
                    )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "recovery_preflight_completed",
                    "Recovery workspace initialization completed.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this recovery operation.",
                        result_summary_extra={
                            "terraform_recovery_enabled": recovery_enabled,
                            "terraform_recovery_executed": False,
                        },
                    )

                # Execution remains disabled unless explicitly enabled.
                if not recovery_enabled:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "TERRAFORM_RECOVERY_DISABLED",
                        (
                            "Recovery preflight completed successfully. "
                            "Terraform recovery execution remains disabled "
                            "because its feature flag is off."
                        ),
                        preflight_passed=True,
                        result_summary_extra={
                            "terraform_recovery_enabled": False,
                            "terraform_recovery_executed": False,
                            "state_snapshot_verified": True,
                            "terraform_initialization_completed": True,
                            "rollback_executed": False,
                        },
                    )

                # Reverify state immediately before executing the plan.
                try:
                    latest_s3_client = create_s3_client(
                        state_record.state_region
                    )
                    latest_snapshot = verify_active_state_snapshot(
                        latest_s3_client,
                        state_record,
                    )
                except (TerraformStateError, S3StateStorageError):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "STATE_SNAPSHOT_VERIFICATION_FAILED",
                        "Terraform state could not be reverified immediately before recovery execution.",
                    )

                if latest_snapshot != plan_snapshot:
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "RECOVERY_STATE_SNAPSHOT_MISMATCH",
                        "Terraform state changed after recovery preflight. Recovery execution was stopped.",
                    )

                if not _record_operation_progress(
                    db,
                    operation_id,
                    execution_token,
                    "terraform_recovery_started",
                    "Executing the separately approved Terraform recovery plan.",
                ):
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "blocked",
                        "WORKER_LEASE_LOST",
                        "Worker no longer owns this recovery operation.",
                    )

                # Execute only the exact approved binary recovery plan.
                try:
                    execute_terraform_saved_plan(
                        workspace,
                        timeout_seconds=600,
                        env=env,
                        lease_renewal_callback=lambda: (
                            _renew_operation_lease(
                                operation_id,
                                execution_token,
                            )
                        ),
                    )
                    recovery_executed = True

                except TerraformExecutionError as exc:
                    # Never reconcile or retry when worker ownership is uncertain.
                    if exc.code in {
                        "WORKER_LEASE_LOST",
                        "WORKER_LEASE_RENEWAL_FAILED",
                    }:
                        return _owned_operation_result(
                            db,
                            operation_id,
                            execution_token,
                            "blocked",
                            exc.code,
                            "Recovery execution stopped after worker ownership was lost. Manual review is required.",
                            result_summary_extra={
                                "terraform_recovery_enabled": True,
                                "terraform_recovery_executed": False,
                                "state_reconciled": False,
                                "recovery_status": "WORKER_OWNERSHIP_UNCERTAIN",
                                "rollback_executed": False,
                            },
                        )

                    # A failed recovery may have partially changed infrastructure.
                    try:
                        reconciliation = _sync_state_after_execution(
                            db,
                            infrastructure_request.id,
                        )
                    except Exception:
                        db.rollback()
                        reconciliation = {
                            "state_reconciled": False,
                            "recovery_status": "MANUAL_REVIEW_REQUIRED",
                        }
                    else:
                        reconciliation["recovery_status"] = (
                            "RECOVERY_EXECUTION_FAILED_RECONCILED"
                        )

                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "failed",
                        exc.code,
                        "Approved Terraform recovery execution failed. No further automatic recovery will be attempted.",
                        preflight_passed=True,
                        result_summary_extra={
                            "terraform_recovery_enabled": True,
                            "terraform_recovery_executed": False,
                            "recovery_required": True,
                            "rollback_executed": False,
                            **reconciliation,
                        },
                    )

                # Terraform succeeded. Synchronize state and inventory.
                try:
                    reconciliation = _sync_state_after_execution(
                        db,
                        infrastructure_request.id,
                    )
                except Exception:
                    db.rollback()
                    return _owned_operation_result(
                        db,
                        operation_id,
                        execution_token,
                        "failed",
                        "RECOVERY_STATE_RECONCILIATION_FAILED",
                        "Terraform recovery executed, but state and inventory reconciliation requires manual review.",
                        preflight_passed=True,
                        result_summary_extra={
                            "terraform_recovery_enabled": True,
                            "terraform_recovery_executed": True,
                            "state_reconciled": False,
                            "recovery_status": "MANUAL_REVIEW_REQUIRED",
                            "rollback_executed": False,
                        },
                    )

                return _owned_operation_result(
                    db,
                    operation_id,
                    execution_token,
                    "succeeded",
                    "NONE",
                    "Approved Terraform recovery completed and infrastructure inventory was synchronized.",
                    preflight_passed=True,
                    result_summary_extra={
                        "terraform_recovery_enabled": True,
                        "terraform_recovery_executed": True,
                        "deployment_succeeded": True,
                        "recovery_status": "RECOVERY_EXECUTED",
                        "rollback_executed": True,
                        **reconciliation,
                    },
                )

        except TerraformExecutionError as exc:
            return _owned_operation_result(
                db,
                operation_id,
                execution_token,
                "failed",
                exc.code,
                "Terraform recovery preflight failed.",
                result_summary_extra={
                    "terraform_recovery_enabled": recovery_enabled,
                    "terraform_recovery_executed": recovery_executed,
                },
            )

    except Exception:
        db.rollback()
        return _owned_operation_result(
            db,
            operation_id,
            execution_token,
            "failed",
            "RECOVERY_WORKER_FAILED",
            "An unexpected error occurred during recovery validation or execution.",
            result_summary_extra={
                "terraform_recovery_enabled": recovery_enabled,
            },
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
                        "terraform_destroy_enabled": False,
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


@celery_app.task(name="aiops.scan_scheduled_cleanup_candidates")
def scan_scheduled_cleanup_candidates() -> dict:
    """Identify explicitly opted-in cleanup candidates without deleting them."""

    db = SessionLocal()

    try:
        resources = list(
            db.scalars(
                select(InfrastructureResource).where(
                    InfrastructureResource.is_present.is_(True),
                    InfrastructureResource.mode == "managed",
                )
            ).all()
        )

        active_request_ids = set(
            db.scalars(
                select(InfrastructureOperation.infrastructure_request_id).where(
                    InfrastructureOperation.status.in_(
                        ("queued", "running")
                    )
                )
            ).all()
        )

        # A resource referenced by another present resource must not
        # be considered independently eligible for cleanup.
        depended_on_addresses = {
            dependency
            for resource in resources
            for dependency in (resource.dependencies or [])
            if isinstance(dependency, str)
        }

        candidates = []
        skipped_active_operations = 0
        skipped_dependencies = 0
        skipped_without_opt_in = 0

        for resource in resources:
            if resource.resource_type != "aws_instance":
                continue
            tags = resource.tags or {}

            cleanup_opt_in = any(
                str(key).strip().lower() == "autocleanup"
                and str(value).strip().lower() == "true"
                for key, value in tags.items()
            )

            if not cleanup_opt_in:
                skipped_without_opt_in += 1
                continue

            if resource.infrastructure_request_id in active_request_ids:
                skipped_active_operations += 1
                continue

            if resource.resource_address in depended_on_addresses:
                skipped_dependencies += 1
                continue

            if not resource.resource_id:
                continue

            candidates.append(
                {
                    "infrastructure_request_id": (
                        resource.infrastructure_request_id
                    ),
                    "resource_address": resource.resource_address,
                    "resource_type": resource.resource_type,
                    "resource_id": resource.resource_id,
                }
            )

        return {
            "status": "completed",
            "mode": "read_only",
            "terraform_destroy_enabled": False,
            "scanned_resources": len(resources),
            "candidate_count": len(candidates),
            "skipped_without_opt_in": skipped_without_opt_in,
            "skipped_active_operations": skipped_active_operations,
            "skipped_dependencies": skipped_dependencies,
            "candidates": candidates,
        }

    finally:
        db.close()