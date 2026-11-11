
"""Authenticated API for trusted Terraform plan generation."""

import hashlib
import json
import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.dependencies import get_db
from backend.app.models.conversation import Conversation
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.artifacts import (
    delete_plan_artifact,
    delete_provider_lock_artifact,
    delete_configuration_artifact,
)
from backend.app.services.terraform.backend_config import (
    build_s3_backend_config,
)
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
    execute_terraform_plan,
    execute_terraform_destroy_plan,
)
from backend.app.services.terraform.state_manager import (
    TerraformStateError,
    create_state_record,
    get_state_record,
    update_state_metadata,
)
from backend.app.services.terraform.s3_state_storage import (
    S3StateStorageError,
    create_s3_client,
    parse_terraform_state,
    read_state_object,
)

router = APIRouter(prefix="/terraform", tags=["Terraform Plans"])


def _read_verified_state_snapshot(
    client,
    state_record,
) -> dict:
    """Read and validate the current S3 state against its DB snapshot."""

    if state_record.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Terraform state is not active.",
        )

    if not all(
        (
            state_record.state_version_id,
            state_record.state_lineage,
            state_record.state_sha256,
        )
    ) or state_record.state_serial is None:
        raise HTTPException(
            status_code=409,
            detail="Terraform state snapshot is incomplete.",
        )

    state = read_state_object(
        client,
        state_record.state_bucket,
        state_record.state_key,
    )

    metadata = parse_terraform_state(state["content"])

    snapshot = {
        "version_id": state["version_id"],
        "serial": metadata["serial"],
        "lineage": metadata["lineage"],
        "sha256": state["sha256"],
    }

    if (
        snapshot["version_id"] != state_record.state_version_id
        or snapshot["serial"] != state_record.state_serial
        or snapshot["lineage"] != state_record.state_lineage
        or snapshot["sha256"] != state_record.state_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail="Terraform state has changed. Synchronize inventory before continuing.",
        )

    return snapshot

@router.post(
    "/requests/{infrastructure_request_id}/destroy-plan",
    status_code=201,
)
def create_trusted_terraform_destroy_plan(
    infrastructure_request_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Generate a destroy plan from the latest successful provisioning plan."""

    # 1. Verify that the infrastructure request exists.
    infrastructure_request = db.scalar(
        select(InfrastructureRequest).where(
            InfrastructureRequest.id == infrastructure_request_id
        )
    )

    if infrastructure_request is None:
        raise HTTPException(
            status_code=404,
            detail="Infrastructure request not found.",
        )

    # 2. Verify request ownership.
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

    # 3. Find the latest successful provisioning plan.
    source_plan = db.scalar(
        select(TerraformPlanRecord)
        .where(
            TerraformPlanRecord.infrastructure_request_id
            == infrastructure_request.id,
            TerraformPlanRecord.operation == "provision",
            TerraformPlanRecord.status == "succeeded",
        )
        .order_by(
            desc(TerraformPlanRecord.created_at),
            desc(TerraformPlanRecord.id),
        )
        .limit(1)
    )

    if source_plan is None:
        raise HTTPException(
            status_code=409,
            detail="No successful provisioning plan is available.",
        )

    # 4. Verify that the original Terraform configuration and provider
    # lock artifacts are available.
    if not all(
        (
            source_plan.configuration_artifact_path,
            source_plan.configuration_artifact_sha256,
            source_plan.provider_lock_path,
            source_plan.provider_lock_sha256,
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="The provisioning plan is missing required Terraform artifacts.",
        )

    # 5. Retrieve and verify the existing state record.
    state_record = get_state_record(
        db,
        infrastructure_request.id,
    )

    if state_record is None:
        raise HTTPException(
            status_code=409,
            detail="Terraform state record not found.",
        )

    bucket = os.getenv("TF_STATE_BUCKET", "").strip()

    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="Terraform state backend is not configured.",
        )

    try:
        backend_config = build_s3_backend_config(
            infrastructure_request_id=infrastructure_request.id,
            bucket=bucket,
            region=os.getenv("TF_STATE_REGION", "ap-south-1"),
            prefix=os.getenv("TF_STATE_PREFIX", "training/requests"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Terraform state backend configuration is invalid.",
        ) from exc

    if (
        state_record.backend_type != "s3"
        or state_record.state_bucket != backend_config["bucket"]
        or state_record.state_key != backend_config["key"]
        or state_record.state_region != backend_config["region"]
    ):
        raise HTTPException(
            status_code=409,
            detail="Terraform state backend does not match the existing record.",
        )

    # 6. Verify the actual S3 state against the database snapshot.
    try:
        s3_client = create_s3_client(state_record.state_region)

        initial_snapshot = _read_verified_state_snapshot(
            s3_client,
            state_record,
        )
    except HTTPException:
        raise
    except S3StateStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail="Terraform state could not be verified.",
        ) from exc

    # 7. Generate a destroy plan. This does not execute destruction.
    try:
        result = execute_terraform_destroy_plan(
            configuration_artifact_path=(
                source_plan.configuration_artifact_path
            ),
            configuration_artifact_sha256=(
                source_plan.configuration_artifact_sha256
            ),
            provider_lock_path=source_plan.provider_lock_path,
            provider_lock_sha256=source_plan.provider_lock_sha256,
        )
    except TerraformExecutionError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Terraform destroy-plan generation failed.",
                "error_code": exc.code,
            },
        ) from exc

    artifact_path = result.get("artifact_path")

    # 8. Verify that the state did not change while planning.
    try:
        final_snapshot = _read_verified_state_snapshot(
            s3_client,
            state_record,
        )
    except (HTTPException, S3StateStorageError) as exc:
        if artifact_path:
            try:
                delete_plan_artifact(artifact_path)
            except Exception:
                pass

        if isinstance(exc, HTTPException):
            raise

        raise HTTPException(
            status_code=503,
            detail="Terraform state could not be reverified after planning.",
        ) from exc

    if final_snapshot != initial_snapshot:
        if artifact_path:
            try:
                delete_plan_artifact(artifact_path)
            except Exception:
                pass

        raise HTTPException(
            status_code=409,
            detail="Terraform state changed during destroy-plan generation.",
        )

    # 9. Save a separate deprovision plan record.
    record = TerraformPlanRecord(
        infrastructure_request_id=infrastructure_request.id,
        requester_id=current_user.id,
        operation="deprovision",
        status="succeeded",
        plan_fingerprint=result["plan_fingerprint"],
        specification_fingerprint=(
            source_plan.specification_fingerprint
        ),
        artifact_path=artifact_path,
        artifact_sha256=result.get("artifact_sha256"),
        configuration_artifact_path=(
            source_plan.configuration_artifact_path
        ),
        configuration_artifact_sha256=(
            source_plan.configuration_artifact_sha256
        ),
        provider_lock_path=source_plan.provider_lock_path,
        provider_lock_sha256=source_plan.provider_lock_sha256,
        state_version_id=initial_snapshot["version_id"],
        state_serial=initial_snapshot["serial"],
        state_lineage=initial_snapshot["lineage"],
        state_sha256=initial_snapshot["sha256"],
        plan_summary=result["plan_summary"],
    )

    db.add(record)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()

        if artifact_path:
            try:
                delete_plan_artifact(artifact_path)
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail="Terraform destroy-plan record could not be saved.",
        ) from exc

    db.refresh(record)

    return {
        "id": record.id,
        "infrastructure_request_id": record.infrastructure_request_id,
        "operation": record.operation,
        "status": record.status,
        "plan_fingerprint": record.plan_fingerprint,
        "plan_summary": record.plan_summary,
        "deployment_enabled": False,
        "created_at": record.created_at,
    }

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

    # Build the same request-specific backend configuration
    # used by the Terraform execution service.
    bucket = os.getenv("TF_STATE_BUCKET", "").strip()

    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="Terraform state backend is not configured.",
        )

    try:
        backend_config = build_s3_backend_config(
            infrastructure_request_id=infrastructure_request.id,
            bucket=bucket,
            region=os.getenv("TF_STATE_REGION", "ap-south-1"),
            prefix=os.getenv("TF_STATE_PREFIX", "training/requests"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Terraform state backend configuration is invalid.",
        ) from exc

    # Retrieve or create the state metadata record.
    state_record = get_state_record(
        db,
        infrastructure_request.id,
    )

    if state_record is not None:
        # Never silently change an existing request's state location.
        if (
            state_record.backend_type != "s3"
            or state_record.state_bucket != backend_config["bucket"]
            or state_record.state_key != backend_config["key"]
            or state_record.state_region != backend_config["region"]
        ):
            raise HTTPException(
                status_code=409,
                detail="Terraform state backend does not match the existing record.",
            )
    else:
        try:
            state_record = create_state_record(
                db=db,
                infrastructure_request_id=infrastructure_request.id,
                state_bucket=backend_config["bucket"],
                state_key=backend_config["key"],
                state_region=backend_config["region"],
            )
        except TerraformStateError as exc:
            # Handle a concurrent request creating the same record.
            state_record = get_state_record(
                db,
                infrastructure_request.id,
            )

            if state_record is None:
                raise HTTPException(
                    status_code=409,
                    detail="Terraform state record could not be created.",
                ) from exc

            if (
                state_record.backend_type != "s3"
                or state_record.state_bucket != backend_config["bucket"]
                or state_record.state_key != backend_config["key"]
                or state_record.state_region != backend_config["region"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Terraform state backend does not match the existing record.",
                ) from exc

    # Preserve an already active state record.
    if state_record.status != "active":
        try:
            state_record = update_state_metadata(
                db,
                infrastructure_request.id,
                status="initializing",
            )
        except TerraformStateError as exc:
            raise HTTPException(
                status_code=500,
                detail="Terraform state metadata could not be updated.",
            ) from exc

    # Generate the Terraform plan and persist its binary artifact.
    try:
        result = execute_terraform_plan(
            specification,
            infrastructure_request_id=infrastructure_request.id,
        )
    except TerraformExecutionError as exc:
        # An initialization failure indicates a backend setup problem.
        # Do not mark an existing active state as failed.
        if exc.code.startswith("INIT_") and state_record.status != "active":
            try:
                update_state_metadata(
                    db,
                    infrastructure_request.id,
                    status="failed",
                )
            except TerraformStateError:
                db.rollback()

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
        provider_lock_path=result.get("provider_lock_path"),
        provider_lock_sha256=result.get("provider_lock_sha256"),
        configuration_artifact_path=result.get("configuration_artifact_path"),
        configuration_artifact_sha256=result.get("configuration_artifact_sha256"),
        plan_summary=result["plan_summary"],
    )

    db.add(record)

    # Commit and refresh are deliberately separate operations.
    # A refresh failure must not delete artifacts for a committed record.
    try:
        db.commit()
    except Exception as exc:
        db.rollback()

        # Remove both saved artifacts if database persistence fails.
        # Do not expose filesystem details to the client.
        artifact_path = result.get("artifact_path")
        provider_lock_path = result.get("provider_lock_path")
        configuration_artifact_path = result.get("configuration_artifact_path")

        if artifact_path:
            try:
                delete_plan_artifact(artifact_path)
            except Exception:
                pass

        if provider_lock_path:
            try:
                delete_provider_lock_artifact(provider_lock_path)
            except Exception:
                pass

        if configuration_artifact_path:
            try:
                delete_configuration_artifact(
                    configuration_artifact_path
                )
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail="Terraform plan record could not be saved.",
        ) from exc

    # The database record is already committed at this point.
    try:
        db.refresh(record)
    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Terraform plan was saved but could not be refreshed.",
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

    payload = specification.model_dump(mode="json")
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()
