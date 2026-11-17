
import logging
from typing import Any

from pydantic import ValidationError

from backend.app.schemas.infrastructure import (
    InfrastructureRequirements,
    ValidatedInfrastructureSpecification,
)
from backend.app.services.ai.client import (
    extract_requirements_with_ai,
    generate_clarification_question_with_ai,
)
from backend.app.services.requirements.gathering import (
    QUESTIONS,
    _first_missing_field,
    gather_requirements,
)
from backend.app.services.ai.resource_sizing import (
    recommend_resource_sizing,
)


logger = logging.getLogger(__name__)

def _include_sizing_recommendation(
    result: dict[str, Any],
    user_message: str,
) -> dict[str, Any]:
    """Attach an indicative sizing recommendation without changing requirements."""

    requirements = result.get("requirements", {})

    # Do not recommend over values already supplied by the user.
    if (
        requirements.get("vcpus") is not None
        or requirements.get("memory_gb") is not None
    ):
        return result

    recommendation = recommend_resource_sizing(user_message)

    if recommendation is None:
        return result

    result["sizing_recommendation"] = recommendation

    if result.get("status") == "needs_clarification":
        question = result.get("question") or ""

        result["question"] = (
            f"{question}\n\n"
            f"Sizing recommendation: {recommendation['vcpus']} vCPUs "
            f"and {recommendation['memory_gb']} GB RAM. "
            "You can provide these values when asked about CPU and memory."
        )

    return result

async def gather_requirements_with_ai(
    message: str,
    current: InfrastructureRequirements | None = None,
) -> dict[str, Any]:
    """Use AI-assisted extraction and contextual clarification.

    Deterministic extraction, missing-field detection and validation remain
    authoritative. AI failures fall back to existing deterministic behavior.
    """
    try:
        extracted = await extract_requirements_with_ai(message, current)
        ai_requirements = InfrastructureRequirements.model_validate(extracted)

        deterministic = gather_requirements(message, current)
        merged = dict(deterministic["requirements"])

        # AI may fill fields that deterministic extraction did not identify.
        # It cannot overwrite explicitly extracted deterministic values.
        for field, value in ai_requirements.model_dump().items():
            if value is not None and merged.get(field) is None:
                merged[field] = value

        # Normalize the AWS Mumbai display name after AI and
        # deterministic requirements have been merged.
        if (
            merged.get("cloud_provider") == "aws"
            and isinstance(merged.get("region"), str)
            and merged["region"].strip().lower() == "mumbai"
        ):
            merged["region"] = "ap-south-1"

        merged_model = InfrastructureRequirements.model_validate(merged)

        result = await gather_requirements_from_collected_requirements(
            merged_model,
            message,
        )

        return _include_sizing_recommendation(result, message)

    except Exception:
        logger.warning(
            "AI requirement extraction failed; using deterministic fallback.",
            exc_info=True,
        )
        result = gather_requirements(message, current)
        return _include_sizing_recommendation(result, message)


async def gather_requirements_from_collected_requirements(
    requirements: InfrastructureRequirements,
    user_message: str = "",
) -> dict[str, Any]:
    """Validate collected requirements and generate a contextual question."""
    collected = requirements.model_dump()
    missing_field = _first_missing_field(collected)

    if missing_field:
        question = QUESTIONS[missing_field]

        try:
            generated_question = await generate_clarification_question_with_ai(
                missing_field=missing_field,
                requirements=collected,
                user_message=user_message,
            )

            if isinstance(generated_question, str) and generated_question.strip():
                question = generated_question.strip()

        except Exception:
            logger.warning(
                "AI clarification generation failed; using fixed question.",
                exc_info=True,
            )

        return {
            "status": "needs_clarification",
            "missing_field": missing_field,
            "question": question,
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
