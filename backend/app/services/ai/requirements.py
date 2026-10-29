
import logging
from typing import Any

from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.ai.client import extract_requirements_with_ai
from backend.app.services.requirements.gathering import gather_requirements


logger = logging.getLogger(__name__)


async def gather_requirements_with_ai(
    message: str,
    current: InfrastructureRequirements | None = None,
) -> dict[str, Any]:
    """Use AI-assisted extraction with deterministic requirement gathering.

    If the AI service is unavailable or returns invalid data, fall back to
    the existing deterministic requirement parser.

    This service does not execute Terraform or provision infrastructure.
    """
    try:
        extracted = await extract_requirements_with_ai(message, current)
        ai_requirements = InfrastructureRequirements.model_validate(extracted)

        # The existing deterministic parser remains authoritative.
        # Apply the user's message to the current requirements first.
        deterministic = gather_requirements(message, current)

        # Merge only AI-extracted values that the deterministic parser
        # did not identify, without replacing explicit deterministic values.
        merged = dict(deterministic["requirements"])

        for field, value in ai_requirements.model_dump().items():
            if value is not None and merged.get(field) is None:
                merged[field] = value

        # Re-run deterministic validation and clarification logic using the
        # merged requirements. Do not bypass the existing schema.
        merged_model = InfrastructureRequirements.model_validate(merged)

        return gather_requirements_from_collected_requirements(
            merged_model
        )

    except Exception:
        logger.warning(
            "AI requirement extraction failed; using deterministic fallback.",
            exc_info=True,
        )
        return gather_requirements(message, current)


def gather_requirements_from_collected_requirements(
    requirements: InfrastructureRequirements,
) -> dict[str, Any]:
    """Apply the existing validation and clarification rules to collected values."""
    from backend.app.services.requirements.gathering import (
        REQUIRED_FIELDS,
        QUESTIONS,
        _first_missing_field,
    )
    from backend.app.schemas.infrastructure import (
        ValidatedInfrastructureSpecification,
    )
    from pydantic import ValidationError

    collected = requirements.model_dump()
    missing_field = _first_missing_field(collected)

    if missing_field:
        return {
            "status": "needs_clarification",
            "missing_field": missing_field,
            "question": QUESTIONS[missing_field],
            "requirements": collected,
        }

    try:
        specification = ValidatedInfrastructureSpecification.model_validate(
            collected
        )
    except ValidationError as exc:
        return {
            "status": "needs_clarification",
            "question": (
                "Some requirements are invalid. Please review the values "
                "and provide valid details."
            ),
            "validation_errors": exc.errors(include_input=False),
            "requirements": collected,
        }

    return {
        "status": "complete",
        "question": None,
        "requirements": specification.model_dump(),
    }
