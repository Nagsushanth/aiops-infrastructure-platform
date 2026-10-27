
"""Build a local, reviewable preview of proposed Terraform resources.

This module does not execute Terraform or provision cloud resources.
"""

from decimal import Decimal
from typing import Any

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.cost_estimator import (
    estimate_ec2_monthly_cost,
)
from backend.app.services.terraform.generator import (
    INSTANCE_CATALOG,
    TerraformGenerationError,
    generate_terraform,
)


def build_plan_preview(
    specification: ValidatedInfrastructureSpecification,
    pricing_catalog: dict[str, Any] | None = None,
    budget_threshold: Decimal | int | float | str | None = None,
) -> dict[str, Any]:
    """Return a proposed-resource summary and a transparent cost estimate.

    Pricing is supplied by trusted server-side code. The default empty
    catalog deliberately produces an unavailable estimate, not a guessed rate.
    """

    terraform_configuration = generate_terraform(specification)

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

    data_sources = [
        "data.aws_ami.amazon_linux_2023",
        "data.aws_vpc.default",
        "data.aws_subnets.default_vpc",
    ]

    instance_type = INSTANCE_CATALOG.get(
        (specification.vcpus, specification.memory_gb)
    )

    if instance_type is None:
        raise TerraformGenerationError(
            "No supported instance type matches the requested CPU and memory."
        )

    cost_estimate = estimate_ec2_monthly_cost(
        instance_type=instance_type,
        region=specification.region,
        pricing_catalog=pricing_catalog or {},
        budget_threshold=budget_threshold,
    )

    estimated_monthly_cost = cost_estimate[
        "estimated_monthly_compute_cost"
    ]

    if cost_estimate["status"] == "estimated":
        cost_estimate_note = (
            "Compute-only estimate based on the configured pricing catalog. "
            "It excludes additional AWS charges and is not a bill prediction."
        )
    else:
        cost_estimate_note = (
            "Not calculated: no verified price is configured for this "
            "instance type and region."
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
        # Retained for compatibility with existing clients and tests.
        "estimated_monthly_cost": estimated_monthly_cost,
        "cost_estimate_note": cost_estimate_note,
        # New structured information for the review UI and budget workflow.
        "cost_estimate": cost_estimate,
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
