
"""Authenticated API for trusted Terraform plan generation."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
    execute_terraform_plan,
)
from backend.app.services.terraform.artifacts import (
    delete_plan_artifact,
)

router = APIRouter(prefix="/terraform", tags=["Terraform Plans"])


@router.post("/requests/{infrastructure_request_id}/plan", status_code=201)
def create_trusted_terraform_plan(
    infrastructure_request_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Generate a real Terraform plan for an owned, completed request."""

    infrastructure_request = db.scalar(
        select(InfrastructureRequest).where(
            InfrastructureRequest.id == infrastructure_request_id
        )
    )

    # Do not disclose whether another user's request exists.
    if infrastructure_request is None:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure request not found.",
        )

    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == infrastructure_request.conversation_id
        )
    )

    if conversation is None or conversation.user_id != current_user.id:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure request not found.",
        )

    if infrastructure_request.status != "complete":
        raise HTTPException(
            status_code=409,
            detail="Infrastructure requirements are not complete.",
        )

    try:
        specification = ValidatedInfrastructureSpecification.model_validate(
            infrastructure_request.requirements
        )
    except Exception as exc:
        # Do not return validation details that might expose stored data.
        raise HTTPException(
            status_code=422,
            detail="Stored infrastructure requirements are invalid.",
        ) from exc

    # Generate the Terraform plan and persist its binary artifact.
    try:
        result = execute_terraform_plan(specification)
    except TerraformExecutionError as exc:
        failed_record = TerraformPlanRecord(
            infrastructure_request_id=infrastructure_request.id,
            requester_id=current_user.id,
            status="failed",
            specification_fingerprint=(
                _fingerprint_specification(specification)
            ),
            plan_summary={},
            error_code=exc.code,
        )

        db.add(failed_record)
        db.commit()

        raise HTTPException(
            status_code=422,
            detail={
                "message": "Terraform plan generation failed.",
                "plan_record_id": failed_record.id,
                "error_code": exc.code,
            },
        ) from exc

    # Store artifact metadata in PostgreSQL.
    # Never return the private artifact path or binary plan to the client.
    record = TerraformPlanRecord(
        infrastructure_request_id=infrastructure_request.id,
        requester_id=current_user.id,
        status="succeeded",
        plan_fingerprint=result["plan_fingerprint"],
        specification_fingerprint=result["specification_fingerprint"],
        artifact_path=result.get("artifact_path"),
        artifact_sha256=result.get("artifact_sha256"),
        plan_summary=result["plan_summary"],
    )

    db.add(record)

    try:
        db.commit()
        db.refresh(record)

    except Exception as exc:
        db.rollback()

        # Remove the saved artifact if its database record cannot
        # be persisted. Do not expose filesystem details to the client.
        artifact_path = result.get("artifact_path")

        if artifact_path:
            try:
                delete_plan_artifact(artifact_path)
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail="Terraform plan record could not be saved.",
        ) from exc

    return {
        "id": record.id,
        "infrastructure_request_id": record.infrastructure_request_id,
        "status": record.status,
        "plan_fingerprint": record.plan_fingerprint,
        "specification_fingerprint": record.specification_fingerprint,
        "plan_summary": record.plan_summary,
        "deployment_enabled": False,
        "created_at": record.created_at,
    }


def _fingerprint_specification(
    specification: ValidatedInfrastructureSpecification,
) -> str:
    """Use the same canonical representation as the execution service."""
    import hashlib
    import json

    payload = specification.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()
