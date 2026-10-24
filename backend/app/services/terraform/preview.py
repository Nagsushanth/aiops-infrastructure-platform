
"""Build a local, reviewable preview of proposed Terraform resources.

This module does not execute Terraform or provision cloud resources.
"""

from typing import Any

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import (
    TerraformGenerationError,
    generate_terraform,
)


def build_plan_preview(
    specification: ValidatedInfrastructureSpecification,
) -> dict[str, Any]:
    """Return a proposed-resource summary for the validated specification."""

    # Reuse the existing generator as the source of supported configurations.
    terraform_configuration = generate_terraform(specification)

    # The initial generator creates these two managed resources.
    resources = [
        {
            "address": "aws_security_group.instance",
            "type": "aws_security_group",
            "name": "instance",
            "action": "create",
            "description": (
                "Create a security group with no inbound rules "
                "and unrestricted outbound traffic."
            ),
        },
        {
            "address": "aws_instance.this",
            "type": "aws_instance",
            "name": "this",
            "action": "create",
            "description": (
                "Create an EC2 instance using the reviewed instance catalog."
            ),
        },
    ]

    # Data sources are read during a real Terraform plan; they are not
    # managed resources being created by this preview.
    data_sources = [
        "data.aws_ami.amazon_linux_2023",
        "data.aws_vpc.default",
        "data.aws_subnets.default_vpc",
    ]

    instance_catalog = {
        (2, 2): "t3.small",
        (2, 4): "t3.medium",
        (2, 8): "t3.large",
        (4, 8): "c6i.xlarge",
        (4, 16): "m6i.xlarge",
        (8, 16): "c6i.2xlarge",
        (8, 32): "m6i.2xlarge",
    }
    instance_type = instance_catalog.get(
        (specification.vcpus, specification.memory_gb)
    )

    if instance_type is None:
        # Normally the generator raises first; retain this guard so the
        # preview never returns a misleading instance size.
        raise TerraformGenerationError(
            "No supported instance type matches the requested CPU and memory."
        )

    return {
        "preview_type": "local_proposed_resource_preview",
        "is_real_terraform_plan": False,
        "deployment_enabled": False,
        "status": "ready_for_review",
        "cloud_provider": specification.cloud_provider,
        "region": specification.region,
        "environment": specification.environment,
        "resource_summary": {
            "create": len(resources),
            "update": 0,
            "delete": 0,
            "total_managed_resources": len(resources),
        },
        "resources": resources,
        "data_sources": data_sources,
        "configuration": {
            "resource_type": specification.resource_type,
            "instance_type": instance_type,
            "vcpus": specification.vcpus,
            "memory_gb": specification.memory_gb,
            "operating_system": (
                specification.operating_system or "linux"
            ),
            "public_ip_associated": specification.public_access,
            "inbound_security_group_rules": 0,
            "root_volume_encrypted": True,
            "imds_v2_required": True,
        },
        "terraform_configuration": terraform_configuration,
        "estimated_monthly_cost": None,
        "cost_estimate_note": (
            "Not calculated. A reliable estimate requires pricing data, "
            "usage assumptions, and environment-specific validation."
        ),
        "approval": {
            "technical_review": "pending",
            "cost_review": "pending",
            "security_review": "pending",
        },
        "warnings": [
            "This is a local proposed-resource preview, not Terraform plan output.",
            "No AWS resources have been created or modified.",
            "The generated configuration expects a default VPC and an available subnet.",
            "Instance availability and actual costs have not been verified.",
            "Public IP association does not by itself make the instance reachable; "
            "network routes and other controls also matter.",
        ],
    }
