
"""API endpoints for Terraform previews and plan parsing."""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.core.auth_dependencies import CurrentUser
from backend.app.core.terraform_pricing import (
    BUDGET_THRESHOLD_USD,
    PRICING_CATALOG,
)
from backend.app.schemas.infrastructure import (
    TerraformPlanParseRequest,
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import TerraformGenerationError
from backend.app.services.terraform.plan_parser import (
    TerraformPlanParsingError,
    parse_terraform_plan,
)
from backend.app.services.terraform.preview import build_plan_preview

router = APIRouter(prefix="/terraform", tags=["Terraform"])


@router.post("/preview")
def create_terraform_preview(
    specification: ValidatedInfrastructureSpecification,
    current_user: CurrentUser,
):
    """Generate a local proposed-resource preview without provisioning."""
    try:
        return build_plan_preview(
            specification,
            pricing_catalog=PRICING_CATALOG,
            budget_threshold=BUDGET_THRESHOLD_USD,
        )
    except TerraformGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        # A bad trusted pricing configuration is a server-side issue,
        # not an invalid user specification. Do not expose internal details.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cost estimation configuration is invalid.",
        ) from exc


@router.post("/plan/parse")
def parse_terraform_plan_endpoint(
    request: TerraformPlanParseRequest,
    current_user: CurrentUser,
):
    """Parse supplied Terraform plan JSON without running or applying it."""
    try:
        return parse_terraform_plan(request.plan)
    except TerraformPlanParsingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
