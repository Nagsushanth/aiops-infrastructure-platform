
"""Generate safe, reviewable Terraform configuration for supported AWS EC2 requests."""

import json
import re

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)


class TerraformGenerationError(ValueError):
    """Raised when a request cannot be safely represented by this generator."""


# Exact CPU and memory matches only. This is a small initial catalog,
# not a claim that these are the only EC2 instance types available.
INSTANCE_CATALOG = {
    (2, 2): "t3.small",
    (2, 4): "t3.medium",
    (2, 8): "t3.large",
    (4, 8): "c6i.xlarge",
    (4, 16): "m6i.xlarge",
    (8, 16): "c6i.2xlarge",
    (8, 32): "m6i.2xlarge",
}


def _terraform_string(value: str) -> str:
    """Encode a string as a valid Terraform-compatible quoted string."""
    return json.dumps(value)


def generate_terraform(
    specification: ValidatedInfrastructureSpecification,
) -> str:
    """Generate Terraform for an exactly supported AWS EC2 specification.

    This function only generates configuration. It never runs Terraform
    commands or creates cloud resources.
    """
    if specification.cloud_provider != "aws":
        raise TerraformGenerationError(
            "The initial generator supports AWS only."
        )

    if specification.resource_type.strip().lower() != "ec2":
        raise TerraformGenerationError(
            "The initial generator supports EC2 only."
        )

    if not re.fullmatch(
        r"[a-z]{2}(?:-[a-z]+)*-\d+",
        specification.region,
    ):
        raise TerraformGenerationError(
            "The region format is invalid."
        )

    instance_type = INSTANCE_CATALOG.get(
        (specification.vcpus, specification.memory_gb)
    )

    if instance_type is None:
        supported = ", ".join(
            f"{cpu} vCPU / {memory} GB"
            for cpu, memory in sorted(INSTANCE_CATALOG)
        )
        raise TerraformGenerationError(
            f"No exact CPU/memory match exists in the initial catalog "
            f"for {specification.vcpus} vCPU / "
            f"{specification.memory_gb} GB. "
            f"Supported combinations: {supported}. "
            "Choose a supported size or extend the reviewed catalog."
        )

    operating_system = (
        (specification.operating_system or "linux").strip().lower()
    )
    if operating_system not in {"linux", "amazon linux", "amazon linux 2023"}:
        raise TerraformGenerationError(
            "The initial generator supports Amazon Linux 2023 only."
        )

    region = _terraform_string(specification.region)
    environment = _terraform_string(specification.environment)
    resource_type = _terraform_string("EC2")
    instance_type_literal = _terraform_string(instance_type)

    public_ip = "true" if specification.public_access else "false"

    return f'''terraform {{
  required_version = ">= 1.5.0, < 2.0.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = {region}
}}

data "aws_ami" "amazon_linux_2023" {{
  most_recent = true
  owners      = ["amazon"]

  filter {{
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }}

  filter {{
    name   = "architecture"
    values = ["x86_64"]
  }}

  filter {{
    name   = "state"
    values = ["available"]
  }}
}}

data "aws_vpc" "default" {{
  default = true
}}

data "aws_subnets" "default_vpc" {{
  filter {{
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }}
}}

resource "aws_security_group" "instance" {{
  name_prefix = "aiops-ec2-"
  description = "AIOps-generated EC2 security group; no inbound rules"
  vpc_id      = data.aws_vpc.default.id

  egress {{
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  tags = {{
    Name        = "aiops-ec2-security-group"
    Environment = {environment}
    ManagedBy   = "AIOps"
  }}
}}

resource "aws_instance" "this" {{
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = {instance_type_literal}
  subnet_id                   = data.aws_subnets.default_vpc.ids[0]
  vpc_security_group_ids      = [aws_security_group.instance.id]
  associate_public_ip_address = {public_ip}

  metadata_options {{
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }}

  root_block_device {{
    encrypted = true
  }}

  tags = {{
    Name        = "aiops-managed-instance"
    Environment = {environment}
    Resource    = {resource_type}
    ManagedBy   = "AIOps"
  }}
}}
'''
