
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.ai.client import AIConfigurationError
from backend.app.services.ai.requirements import gather_requirements_with_ai
from backend.app.services.requirements.gathering import gather_requirements

@pytest.fixture(autouse=True)
def mock_ai_clarification_generation():
    with patch(
        "backend.app.services.ai.requirements.generate_clarification_question_with_ai",
        new_callable=AsyncMock,
        return_value="Could you provide the missing infrastructure detail?",
    ):
        yield

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

def test_ai_generated_clarification_question_is_used():
    extracted = {"cloud_provider": "aws"}

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ), patch(
        "backend.app.services.ai.requirements.generate_clarification_question_with_ai",
        new_callable=AsyncMock,
        return_value="What type of EC2 instance do you need?",
    ) as mock_question:
        result = asyncio.run(
            gather_requirements_with_ai("I need an AWS instance")
        )

    assert result["status"] == "needs_clarification"
    assert result["question"] == "What type of EC2 instance do you need?"
    assert result["missing_field"] == "resource_type"
    mock_question.assert_awaited_once()

    call_kwargs = mock_question.await_args.kwargs

    assert call_kwargs["missing_field"] == "resource_type"
    assert call_kwargs["user_message"] == "I need an AWS instance"
    assert call_kwargs["requirements"]["cloud_provider"] == "aws"

def test_question_generation_failure_uses_fixed_fallback():
    extracted = {"cloud_provider": "aws"}

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ), patch(
        "backend.app.services.ai.requirements.generate_clarification_question_with_ai",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Simulated question generation failure"),
    ):
        result = asyncio.run(
            gather_requirements_with_ai("I need an AWS instance")
        )

    assert result["status"] == "needs_clarification"
    assert result["missing_field"] == "resource_type"
    assert result["question"] == (
        "What type of resource do you need, such as an EC2 instance?"
    )

def test_complete_requirements_do_not_generate_clarification():
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
    ), patch(
        "backend.app.services.ai.requirements.generate_clarification_question_with_ai",
        new_callable=AsyncMock,
    ) as mock_question:
        result = asyncio.run(
            gather_requirements_with_ai("Create a development instance")
        )

    assert result["status"] == "complete"
    assert result["question"] is None
    mock_question.assert_not_awaited()


def test_workload_sizing_recommendation_is_included():
    extracted = {"cloud_provider": "aws"}

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "I need a small development web application"
            )
        )

    recommendation = result["sizing_recommendation"]

    assert recommendation["vcpus"] == 2
    assert recommendation["memory_gb"] == 4
    assert "Sizing recommendation" in result["question"]


def test_explicit_cpu_and_memory_prevent_sizing_recommendation():
    extracted = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 4,
        "memory_gb": 8,
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
                "I need a small development web application "
                "with 4 vCPUs and 8 GB RAM"
            )
        )

    assert result["requirements"]["vcpus"] == 4
    assert result["requirements"]["memory_gb"] == 8
    assert "sizing_recommendation" not in result


def test_unknown_workload_does_not_receive_sizing_recommendation():
    extracted = {"cloud_provider": "aws"}

    with patch(
        "backend.app.services.ai.requirements.extract_requirements_with_ai",
        new_callable=AsyncMock,
        return_value=extracted,
    ):
        result = asyncio.run(
            gather_requirements_with_ai(
                "I need an infrastructure resource"
            )
        )

    assert "sizing_recommendation" not in result

def test_ai_mumbai_region_is_normalized_for_aws():
    extracted = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "mumbai",
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
                "Create a private development instance"
            )
        )

    assert result["status"] == "complete"
    assert result["requirements"]["cloud_provider"] == "aws"
    assert result["requirements"]["region"] == "ap-south-1"