
import re
from typing import Any

from pydantic import ValidationError

from backend.app.schemas.infrastructure import (
    InfrastructureRequirements,
    ValidatedInfrastructureSpecification,
)


REQUIRED_FIELDS = [
    "cloud_provider",
    "resource_type",
    "environment",
    "region",
    "vcpus",
    "memory_gb",
    "public_access",
    "operating_system",
]

QUESTIONS = {
    "cloud_provider": "Which cloud provider should we use: AWS, Azure, or GCP?",
    "resource_type": "What type of resource do you need, such as an EC2 instance?",
    "environment": (
        "Which environment is this for: development, testing, staging, or production?"
    ),
    "region": "Which cloud region should we use?",
    "vcpus": "How many vCPUs do you need?",
    "memory_gb": "How much memory do you need in GB?",
    "public_access": (
        "Should this resource be publicly accessible? Please answer yes or no."
    ),
    "operating_system": (
        "Which operating system should we use, such as Linux or Windows?"
    ),
}


def _first_missing_field(requirements: dict[str, Any]) -> str | None:
    for field in REQUIRED_FIELDS:
        value = requirements.get(field)
        if value is None or (
            isinstance(value, str) and not value.strip()
        ):
            return field
    return None


def extract_requirements(
    message: str,
    current: InfrastructureRequirements | None = None,
) -> InfrastructureRequirements:
    requirements = (
        current.model_dump()
        if current is not None
        else InfrastructureRequirements().model_dump()
    )

    text = message.strip()
    lower = text.lower()
    missing_field = _first_missing_field(requirements)

    # Cloud provider
    if re.search(r"\baws\b|\bec2\b|\bs3\b|\brds\b", lower):
        requirements["cloud_provider"] = "aws"
    elif re.search(r"\bazure\b", lower):
        requirements["cloud_provider"] = "azure"
    elif re.search(r"\bgcp\b|google cloud", lower):
        requirements["cloud_provider"] = "gcp"

    # Resource type
    resource_patterns = [
        (r"\bec2\b|virtual machine|\bvm\b", "EC2"),
        (r"\bs3\b|object storage", "S3"),
        (r"\brds\b|managed database", "RDS"),
    ]
    for pattern, resource in resource_patterns:
        if re.search(pattern, lower):
            requirements["resource_type"] = resource
            break

    # Operating system
    if re.search(r"\bwindows?\b|\bwin\b", lower):
        requirements["operating_system"] = "Windows"
    elif re.search(
        r"\blinux\b|\bubuntu\b|\bdebian\b|\bred hat\b|\brhel\b|"
        r"\bamazon linux\b",
        lower,
    ):
        requirements["operating_system"] = "Linux"
    elif missing_field == "operating_system":
        if lower in {
            "linux", "ubuntu", "debian", "rhel", "red hat", "amazon linux"
        }:
            requirements["operating_system"] = "Linux"
        elif lower in {"windows", "windows server", "win"}:
            requirements["operating_system"] = "Windows"

    # Environment
    for environment in ("development", "testing", "staging", "production"):
        if re.search(rf"\b{environment}\b", lower):
            requirements["environment"] = environment
            break

    # Region
    region_match = re.search(
        r"\b([a-z]{2}(?:-gov)?-[a-z]+-\d+)\b",
        lower,
    )
    if region_match:
        requirements["region"] = region_match.group(1)

    # vCPUs
    cpu_match = re.search(
        r"\b(\d+)\s*(?:vcpus?|cpus?|cores?)\b",
        lower,
    )
    if cpu_match:
        requirements["vcpus"] = int(cpu_match.group(1))
    elif missing_field == "vcpus" and re.fullmatch(r"\d+", lower):
        requirements["vcpus"] = int(lower)

    # Memory
    memory_match = re.search(
        r"\b(\d+)\s*(?:gb|gib)\s*(?:of\s*)?(?:ram|memory)?\b",
        lower,
    )
    if memory_match:
        requirements["memory_gb"] = int(memory_match.group(1))
    elif missing_field == "memory_gb" and re.fullmatch(r"\d+", lower):
        requirements["memory_gb"] = int(lower)

    # Public access
    if re.search(
        r"\b(private|not publicly accessible|no public access|no public ip|"
        r"without public access|not public)\b",
        lower,
    ):
        requirements["public_access"] = False
    elif re.search(
        r"\b(publicly accessible|public access|public ip|publicly exposed)\b",
        lower,
    ):
        requirements["public_access"] = True
    elif missing_field == "public_access":
        if re.fullmatch(r"(yes|y|true)", lower):
            requirements["public_access"] = True
        elif re.fullmatch(r"(no|n|false)", lower):
            requirements["public_access"] = False

    return InfrastructureRequirements.model_validate(requirements)


def gather_requirements(
    message: str,
    current: InfrastructureRequirements | None = None,
) -> dict[str, Any]:
    existing = (
        current.model_dump()
        if current is not None
        else InfrastructureRequirements().model_dump()
    )

    if not message.strip():
        return {
            "status": "needs_input",
            "question": "Please describe the infrastructure you want to provision.",
            "requirements": existing,
        }

    try:
        requirements = extract_requirements(message, current)
    except ValidationError as exc:
        return {
            "status": "needs_clarification",
            "question": (
                "One or more values are invalid or outside the allowed limits. "
                "Please provide valid values."
            ),
            "validation_errors": exc.errors(include_input=False),
            "requirements": existing,
        }

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
