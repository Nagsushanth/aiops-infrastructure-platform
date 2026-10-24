
from backend.app.schemas.infrastructure import InfrastructureRequirements
from backend.app.services.requirements.gathering import gather_requirements


def test_no_public_access_is_false():
    result = gather_requirements(
        "AWS EC2 development instance in region ap-south-1 "
        "with 5 vCPUs and 8 GB memory, no public access, Linux."
    )

    assert result["requirements"]["public_access"] is False
    assert result["status"] == "complete"


def test_yes_answer_when_public_access_is_requested():
    current = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
        region="ap-south-1",
        vcpus=5,
        memory_gb=8,
        operating_system="Linux",
    )

    result = gather_requirements("yes", current)

    assert result["requirements"]["public_access"] is True
    assert result["status"] == "complete"


def test_bare_number_answers_vcpu_question():
    current = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
        region="ap-south-1",
        operating_system="Linux",
    )

    result = gather_requirements("5", current)

    assert result["requirements"]["vcpus"] == 5
    assert result["missing_field"] == "memory_gb"


def test_bare_number_answers_memory_question():
    current = InfrastructureRequirements(
        cloud_provider="aws",
        resource_type="EC2",
        environment="development",
        region="ap-south-1",
        vcpus=5,
        operating_system="Linux",
    )

    result = gather_requirements("8", current)

    assert result["requirements"]["memory_gb"] == 8
    assert result["missing_field"] == "public_access"


def test_out_of_range_vcpu_does_not_crash():
    result = gather_requirements(
        "AWS EC2 development instance in region ap-south-1 "
        "with 300 vCPUs and 8 GB memory, private access, Linux."
    )

    assert result["status"] == "needs_clarification"
    assert "validation_errors" in result
