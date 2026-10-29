
import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.ai.client import (
    AIConfigurationError,
    create_ai_client,
    extract_requirements_with_ai,
)


def test_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("BEDROCK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    with pytest.raises(AIConfigurationError, match="API key"):
        create_ai_client()


def test_client_requires_endpoint(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    with pytest.raises(AIConfigurationError, match="base URL"):
        create_ai_client()

def test_extracts_and_validates_structured_requirements(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

    payload = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 2,
        "memory_gb": 2,
        "public_access": False,
        "operating_system": "Linux",
    }

    fake_response = MagicMock()
    fake_response.choices[0].message.content = json.dumps(payload)

    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=fake_response
    )

    with patch(
        "backend.app.services.ai.client.create_ai_client",
        return_value=(fake_client, "test-model"),
    ):
        result = asyncio.run(
            extract_requirements_with_ai(
                "Private AWS EC2 development instance in ap-south-1",
                InfrastructureRequirements(),
        )
    )

    assert result == payload
    fake_client.chat.completions.create.assert_awaited_once()


def test_rejects_invalid_model_output(monkeypatch):
    fake_response = MagicMock()
    fake_response.choices[0].message.content = '{"vcpus": "many"}'

    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=fake_response
    )

    with patch(
        "backend.app.services.ai.client.create_ai_client",
        return_value=(fake_client, "test-model"),
    ):
        with pytest.raises(ValueError, match="invalid requirement data"):
            asyncio.run(
                extract_requirements_with_ai(
                    "Create an instance",
                    InfrastructureRequirements(),
                )
            )
    

@pytest.mark.parametrize(
    "model_output",
    [
        {
            "cloud_provider": "aws",
            "terraform_apply": True,
        },
        {
            "cloud_provider": "aws",
            "admin_approved": True,
        },
    ],
)
def test_rejects_unexpected_fields(model_output):
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_response = MagicMock()
    fake_response.choices[0].message.content = json.dumps(model_output)

    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=fake_response
    )

    with patch(
        "backend.app.services.ai.client.create_ai_client",
        return_value=(fake_client, "test-model"),
    ):
        with pytest.raises(ValueError, match="unexpected requirement fields"):
            asyncio.run(
                extract_requirements_with_ai("Create an AWS instance")
            )


def test_preserves_private_access_when_model_returns_null():
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_response = MagicMock()
    fake_response.choices[0].message.content = json.dumps(
        {"public_access": None}
    )

    fake_client = MagicMock()
    fake_client.close = AsyncMock()
    fake_client.chat.completions.create = AsyncMock(
        return_value=fake_response
    )

    current = InfrastructureRequirements(public_access=False)

    with patch(
        "backend.app.services.ai.client.create_ai_client",
        return_value=(fake_client, "test-model"),
    ):
        result = asyncio.run(
            extract_requirements_with_ai("Keep it private", current)
        )

    assert result["public_access"] is False

