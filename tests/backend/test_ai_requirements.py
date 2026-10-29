
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.ai.client import AIConfigurationError
from backend.app.services.ai.requirements import gather_requirements_with_ai
from backend.app.services.requirements.gathering import gather_requirements


def test_ai_failure_falls_back_to_deterministic_parser():
    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        side_effect=AIConfigurationError("AI is not configured"),
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "Create a private AWS EC2 development instance"
            )
        )

    assert result["status"] == "needs_clarification"
    assert result["requirements"]["cloud_provider"] == "aws"
    assert result["requirements"]["resource_type"] == "EC2"
    assert result["requirements"]["public_access"] is False


def test_ai_extraction_fills_missing_fields():
    extracted = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": False,
        "operating_system": "Linux",
    }

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "I need a small private Linux instance"
            )
        )

    assert result["status"] == "complete"
    assert result["requirements"]["public_access"] is False
    assert result["requirements"]["cloud_provider"] == "aws"


def test_ai_does_not_override_deterministic_private_access():
    extracted = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": True,
        "operating_system": "Linux",
    }

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "Create a private AWS EC2 development instance"
            )
        )

    assert result["requirements"]["public_access"] is False


def test_ai_failure_preserves_existing_requirements():
    current = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        public_access=False,
    )

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Simulated AI failure"),
    ):
        result = asyncio.run(
            gather_requirements_with_ai("Use Linux", current)
        )

    assert result["requirements"]["cloud_provider"] == "aws"
    assert result["requirements"]["resource_type"] == "EC2"
    assert result["requirements"]["public_access"] is False


def test_ai_does_not_override_existing_requirements():
    current = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
        public_access=False,
    )

    extracted = {
        "cloud_provider": "azure",
        "resource_type": "S3",
        "environment": "production",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 4,
        "public_access": True,
        "operating_system": "Linux",
    }

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "Use Linux",
                current,
            )
        )

    requirements = result["requirements"]

    assert requirements["cloud_provider"] == "aws"
    assert requirements["resource_type"] == "EC2"
    assert requirements["environment"] == "development"
    assert requirements["public_access"] is False
    assert requirements["operating_system"] == "Linux"


def test_invalid_ai_output_falls_back_to_deterministic_parser():
    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value={"cloud_provider": "invalid-cloud"},
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "Create a private AWS EC2 development instance"
            )
        )

    expected = gather_requirements(
        "Create a private AWS EC2 development instance"
    )

    assert result == expected
    assert result["requirements"]["cloud_provider"] == "aws"
    assert result["requirements"]["public_access"] is False
