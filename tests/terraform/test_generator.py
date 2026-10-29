
import pytest

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import (
    TerraformGenerationError,
    generate_terraform,
    write_terraform_configuration,
)


def make_spec(**overrides):
    values = {
        "cloud_provider": "aws",
        "resource_type": "EC2",
        "environment": "development",
        "region": "ap-south-1",
        "vcpus": 4,
        "memory_gb": 8,
        "public_access": False,
        "operating_system": "linux",
    }
    values.update(overrides)
    return ValidatedInfrastructureSpecification(**values)


def test_generates_configuration_for_supported_size():
    result = generate_terraform(make_spec())

    assert 'region = "ap-south-1"' in result
    assert 'instance_type               = "c6i.xlarge"' in result
    assert "associate_public_ip_address = false" in result
    assert 'http_tokens                 = "required"' in result
    assert "encrypted = true" in result


def test_public_access_assigns_public_ip_without_opening_inbound_ports():
    result = generate_terraform(make_spec(public_access=True))

    assert "associate_public_ip_address = true" in result
    assert 'cidr_blocks = ["0.0.0.0/0"]' in result
    assert "ingress {" not in result


def test_rejects_unsupported_cpu_memory_combination():
    with pytest.raises(TerraformGenerationError, match="No exact CPU/memory"):
        generate_terraform(make_spec(vcpus=5, memory_gb=8))


def test_rejects_non_aws_provider():
    with pytest.raises(TerraformGenerationError, match="AWS only"):
        generate_terraform(make_spec(cloud_provider="azure"))


def test_rejects_non_ec2_resource():
    with pytest.raises(TerraformGenerationError, match="EC2 only"):
        generate_terraform(make_spec(resource_type="S3"))


def test_rejects_unsupported_operating_system():
    with pytest.raises(TerraformGenerationError, match="Amazon Linux 2023"):
        generate_terraform(make_spec(operating_system="windows"))


def test_rejects_invalid_region_format():
    with pytest.raises(TerraformGenerationError, match="region format"):
        generate_terraform(make_spec(region="not a region"))

def test_rejects_region_outside_approved_region():
    with pytest.raises(
        TerraformGenerationError,
        match="Only the approved AWS region ap-south-1",
    ):
        generate_terraform(make_spec(region="us-east-1"))


def test_writes_generated_terraform_to_main_tf(tmp_path):
    output_file = write_terraform_configuration(
        make_spec(),
        tmp_path / "generated",
    )

    assert output_file == tmp_path / "generated" / "main.tf"
    assert output_file.is_file()

    content = output_file.read_text(encoding="utf-8")

    assert 'resource "aws_instance" "this"' in content
    assert 'resource "aws_security_group" "instance"' in content


def test_does_not_write_file_for_unsupported_specification(tmp_path):
    output_dir = tmp_path / "generated"

    with pytest.raises(TerraformGenerationError):
        write_terraform_configuration(
            make_spec(vcpus=5, memory_gb=8),
            output_dir,
        )

    assert not output_dir.exists()
