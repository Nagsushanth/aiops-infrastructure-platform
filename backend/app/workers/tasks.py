"""Background tasks for infrastructure lifecycle operations."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from backend.app.core.database import SessionLocal
from backend.app.models.approval import (
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
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    load_plan_artifact,
)
from backend.app.workers.celery_app import celery_app


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


def _block_operation(operation, error_code: str, message: str) -> None:
    operation.status = "blocked"
    operation.error_code = error_code
    operation.completed_at = datetime.now(timezone.utc)
    operation.result_summary = {
        "message": message,
        "preflight_passed": error_code == "TERRAFORM_APPLY_DISABLED",
        "terraform_apply_enabled": False,
    }


@celery_app.task(
    name="aiops.execute_provision_operation",
)
def execute_provision_operation(operation_id: int) -> dict:
    """Revalidate a provision request without executing Terraform apply."""

    db = SessionLocal()

    try:
        operation = db.get(InfrastructureOperation, operation_id)

        if operation is None:
            return {
                "status": "not_found",
                "operation_id": operation_id,
            }

        if operation.status != "queued":
            return {
                "status": operation.status,
                "operation_id": operation_id,
                "message": "Operation is not queued.",
            }

        operation.status = "running"
        operation.started_at = datetime.now(timezone.utc)
        db.commit()

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
            _block_operation(
                operation,
                "OPERATION_RELATIONSHIP_INVALID",
                "The operation references missing or invalid records.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        if (
            approval.requester_id != operation.requester_id
            or approval.infrastructure_request_id
            != infrastructure_request.id
            or approval.terraform_plan_record_id != plan.id
            or plan.infrastructure_request_id != infrastructure_request.id
            or plan.requester_id != operation.requester_id
        ):
            _block_operation(
                operation,
                "OPERATION_OWNERSHIP_MISMATCH",
                "The operation ownership or record relationships are invalid.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        conversation = db.get(
            Conversation,
            infrastructure_request.conversation_id,
        )

        if (
            conversation is None
            or conversation.user_id != operation.requester_id
        ):
            _block_operation(
                operation,
                "REQUEST_OWNERSHIP_MISMATCH",
                "The infrastructure request ownership check failed.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        if approval.status != "approved":
            _block_operation(
                operation,
                "APPROVAL_NOT_FINAL",
                "The approval is no longer finally approved.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        if plan.status != "succeeded":
            _block_operation(
                operation,
                "PLAN_NOT_SUCCEEDED",
                "The Terraform plan is not in succeeded status.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        if (
            not plan.plan_fingerprint
            or approval.plan_fingerprint != plan.plan_fingerprint
        ):
            _block_operation(
                operation,
                "PLAN_FINGERPRINT_MISMATCH",
                "The approved plan fingerprint does not match.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

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
            _block_operation(
                operation,
                "APPROVAL_STAGES_INCOMPLETE",
                "All technical, cost and security reviews must be approved.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        try:
            specification = (
                ValidatedInfrastructureSpecification.model_validate(
                    infrastructure_request.requirements
                )
            )
        except Exception:
            _block_operation(
                operation,
                "SPECIFICATION_INVALID",
                "The infrastructure specification is no longer valid.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        current_fingerprint = _fingerprint_specification(specification)

        if plan.specification_fingerprint != current_fingerprint:
            _block_operation(
                operation,
                "SPECIFICATION_FINGERPRINT_MISMATCH",
                "Infrastructure requirements changed after plan generation.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        if not plan.artifact_path or not plan.artifact_sha256:
            _block_operation(
                operation,
                "PLAN_ARTIFACT_MISSING",
                "The Terraform plan artifact is unavailable.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        try:
            load_plan_artifact(
                plan.artifact_path,
                plan.artifact_sha256,
            )
        except TerraformArtifactError:
            _block_operation(
                operation,
                "PLAN_ARTIFACT_INVALID",
                "Terraform plan artifact integrity verification failed.",
            )
            db.commit()
            return {"status": operation.status, "operation_id": operation_id}

        # Deliberate safety boundary:
        # persistent Terraform state and deployment safeguards must be
        # completed before any real Terraform apply can be enabled.
        _block_operation(
            operation,
            "TERRAFORM_APPLY_DISABLED",
            (
                "Preflight validation succeeded. Terraform apply remains "
                "disabled until persistent state and deployment safeguards "
                "are implemented."
            ),
        )
        db.commit()

        return {
            "status": operation.status,
            "operation_id": operation_id,
            "preflight_passed": True,
            "terraform_apply_enabled": False,
        }

    except Exception:
        db.rollback()

        operation = db.get(
            InfrastructureOperation,
            operation_id,
        )

        if operation is not None:
            _block_operation(
                operation,
                "WORKER_PREFLIGHT_FAILED",
                "An unexpected error occurred during operation preflight.",
            )
            db.commit()

        return {
            "status": "blocked",
            "operation_id": operation_id,
            "error_code": "WORKER_PREFLIGHT_FAILED",
        }

    finally:
        db.close()
