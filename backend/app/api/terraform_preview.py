
"""API endpoint for local Terraform proposed-resource previews."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import TerraformGenerationError
from backend.app.services.terraform.preview import build_plan_preview

router = APIRouter(prefix="/terraform", tags=["Terraform Preview"])


@router.post("/preview")
def create_terraform_preview(
    specification: ValidatedInfrastructureSpecification,
    current_user: CurrentUser,
):
    """Generate a local proposed-resource preview without provisioning."""
    try:
        return build_plan_preview(specification)
    except TerraformGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
