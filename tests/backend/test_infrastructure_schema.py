import pytest
from pydantic import ValidationError

from backend.app.schemas.infrastructure import (
    InfrastructureRequirements,
    ValidatedInfrastructureSpecification,
)


def test_partial_requirements_are_allowed():
    requirements = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
    )

    assert requirements.vcpus is None
    assert requirements.public_access is None


def test_valid_complete_specification():
    specification = ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
        region="ap-south-1",
        vcpus=2,
        memory_gb=4,
        public_access=False,
    )

    assert specification.vcpus == 2
    assert specification.public_access is False


def test_invalid_cpu_count_is_rejected():
    with pytest.raises(ValidationError):
        InfrastructureRequirements(vcpus=0)


def test_incomplete_specification_is_rejected():
    with pytest.raises(ValidationError):
        ValidatedInfrastructureSpecification(
            cloud_provider="aws",
            resource_type="EC2",
            environment="development",
        )