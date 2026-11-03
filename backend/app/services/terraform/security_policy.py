
"""Read-only security policy checks for Terraform plan JSON.

These are initial AWS security checks aligned with selected security
best practices. They are not a complete CIS benchmark assessment.
"""

import json
from typing import Any


# Only resource types reviewed for the current implementation.
APPROVED_RESOURCE_TYPES = {
    "aws_instance",
    "aws_security_group",
    "aws_vpc_security_group_ingress_rule",
    "aws_vpc_security_group_egress_rule",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _finding(
    rule_id: str,
    severity: str,
    address: str,
    title: str,
    detail: str,
    remediation: str,
) -> dict[str, str]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "address": address,
        "title": title,
        "detail": detail,
        "remediation": remediation,
    }


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _is_world_open(cidr: Any) -> bool:
    return cidr in {"0.0.0.0/0", "::/0"}


def _contains_world_open(value: Any) -> bool:
    """Find unrestricted IPv4 or IPv6 CIDRs in nested rule data."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "cidr_blocks",
                "ipv6_cidr_blocks",
            }:
                if any(_is_world_open(cidr) for cidr in _as_list(item)):
                    return True

            # Standalone VPC security-group rule resources use
            # cidr_ipv4 and cidr_ipv6 instead of CIDR lists.
            if key in {"cidr_ipv4", "cidr_ipv6"}:
                if _is_world_open(item):
                    return True

            if _contains_world_open(item):
                return True

    elif isinstance(value, list):
        return any(_contains_world_open(item) for item in value)

    return False


def _decode_policy_document(value: Any) -> Any:
    """Decode a JSON policy document when supplied as a string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None

    return value


def _contains_wildcard(value: Any) -> bool:
    """Check whether a string or list contains a wildcard pattern."""
    if isinstance(value, str):
        return "*" in value

    if isinstance(value, list):
        return any(_contains_wildcard(item) for item in value)

    return False


def _has_wildcard_iam_permission(value: Any) -> bool:
    """Flag Allow statements with wildcard actions or universal resources."""
    document = _decode_policy_document(value)

    if not isinstance(document, dict):
        return False

    statements = document.get("Statement", [])

    if isinstance(statements, dict):
        statements = [statements]

    if not isinstance(statements, list):
        return False

    for statement in statements:
        if not isinstance(statement, dict):
            continue

        # Wildcards in explicit Deny statements do not grant permissions.
        if statement.get("Effect") != "Allow":
            continue

        action = statement.get("Action", [])
        resource = statement.get("Resource", [])

        # Wildcard action patterns such as "*" and "s3:Get*".
        if _contains_wildcard(action):
            return True

        # Flag universal resource access, rather than normal resource
        # ARN patterns such as a bucket's object path.
        if resource == "*" or (
            isinstance(resource, list) and "*" in resource
        ):
            return True

    return False


def _check_ec2(
    address: str,
    after: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """Evaluate supported EC2 instance security settings."""

    if after.get("associate_public_ip_address") is True:
        findings.append(
            _finding(
                "EC2_PUBLIC_IP",
                "high",
                address,
                "EC2 instance has a public IP",
                "The planned instance is configured to receive a public IP.",
                "Disable public IP assignment unless a documented exception is approved.",
            )
        )

    # Require an explicit IMDSv2 configuration.
    metadata = after.get("metadata_options")
    metadata_options = (
        metadata[0]
        if isinstance(metadata, list) and metadata
        else None
    )

    if (
        not isinstance(metadata_options, dict)
        or metadata_options.get("http_tokens") != "required"
    ):
        findings.append(
            _finding(
                "EC2_IMDSV2_NOT_REQUIRED",
                "high",
                address,
                "IMDSv2 is not enforced",
                "Instance metadata tokens are missing or are not explicitly required.",
                "Set metadata_options.http_tokens to required.",
            )
        )

    # Require explicit encryption configuration for the root device.
    root_devices = _as_list(after.get("root_block_device"))

    if not root_devices:
        findings.append(
            _finding(
                "EBS_ROOT_NOT_ENCRYPTED",
                "high",
                address,
                "Root volume encryption is not explicitly configured",
                "The planned instance has no explicit root block device encryption configuration.",
                "Configure root_block_device.encrypted as true.",
            )
        )

    for device in root_devices:
        if not isinstance(device, dict) or device.get("encrypted") is not True:
            findings.append(
                _finding(
                    "EBS_ROOT_NOT_ENCRYPTED",
                    "high",
                    address,
                    "Root volume encryption is not enabled",
                    "A root block device is not explicitly configured as encrypted.",
                    "Set root_block_device.encrypted to true.",
                )
            )


def _check_security_group(
    address: str,
    after: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """Evaluate inline security-group ingress and egress rules."""

    for rule in _as_list(after.get("ingress")):
        if isinstance(rule, dict) and _contains_world_open(rule):
            findings.append(
                _finding(
                    "SECURITY_GROUP_PUBLIC_INGRESS",
                    "high",
                    address,
                    "Security group permits public ingress",
                    "An ingress rule includes an unrestricted IPv4 or IPv6 CIDR.",
                    "Restrict source CIDRs to approved networks and required ports.",
                )
            )

    for rule in _as_list(after.get("egress")):
        if isinstance(rule, dict) and _contains_world_open(rule):
            findings.append(
                _finding(
                    "SECURITY_GROUP_OPEN_EGRESS",
                    "medium",
                    address,
                    "Security group permits unrestricted egress",
                    "An egress rule includes an unrestricted IPv4 or IPv6 CIDR.",
                    "Limit outbound destinations and ports to business requirements.",
                )
            )


def _check_standalone_security_rule(
    address: str,
    resource_type: str,
    after: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """Evaluate standalone VPC security-group rule resources."""

    if not _contains_world_open(after):
        return

    if resource_type == "aws_vpc_security_group_ingress_rule":
        findings.append(
            _finding(
                "SECURITY_GROUP_PUBLIC_INGRESS",
                "high",
                address,
                "Ingress rule allows public access",
                "The planned ingress rule includes an unrestricted IPv4 or IPv6 CIDR.",
                "Restrict the source CIDR and required ports.",
            )
        )

    elif resource_type == "aws_vpc_security_group_egress_rule":
        findings.append(
            _finding(
                "SECURITY_GROUP_OPEN_EGRESS",
                "medium",
                address,
                "Egress rule permits unrestricted outbound access",
                "The planned egress rule includes an unrestricted IPv4 or IPv6 CIDR.",
                "Limit outbound destinations and ports to business requirements.",
            )
        )


def _check_iam_policy(
    address: str,
    after: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """Evaluate IAM policy permissions when an IAM policy is present."""

    policy = after.get("policy")
    document = _decode_policy_document(policy)

    if not isinstance(document, dict):
        return

    if _has_wildcard_iam_permission(document):
        findings.append(
            _finding(
                "IAM_WILDCARD_PERMISSION",
                "high",
                address,
                "IAM policy contains wildcard permissions",
                "An Allow statement contains a wildcard action or universal resource.",
                "Apply least privilege by limiting actions and resources.",
            )
        )


def evaluate_terraform_security(
    plan: dict[str, Any],
    approved_resource_types: set[str] | None = None,
) -> dict[str, Any]:
    """Return security policy findings for Terraform plan JSON.

    This is an initial rule set, not a complete CIS benchmark or
    a replacement for a dedicated security scanner.
    """
    if not isinstance(plan, dict):
        raise ValueError("Terraform plan must be a JSON object.")

    changes = plan.get("resource_changes", [])

    if not isinstance(changes, list):
        raise ValueError("resource_changes must be a list.")

    approved = (
        APPROVED_RESOURCE_TYPES
        if approved_resource_types is None
        else approved_resource_types
    )

    findings: list[dict[str, str]] = []

    for resource in changes:
        if not isinstance(resource, dict):
            raise ValueError("Each resource change must be an object.")

        address = resource.get("address", "unknown")
        resource_type = resource.get("type", "unknown")
        change = resource.get("change", {})

        if not isinstance(change, dict):
            raise ValueError(f"Invalid change object for {address}.")

        actions = change.get("actions", [])

        if not isinstance(actions, list) or not all(
            isinstance(action, str) for action in actions
        ):
            raise ValueError(f"Invalid actions for {address}.")

        # Pure deletions do not create a new configuration to assess.
        if actions == ["delete"]:
            continue

        after = change.get("after")

        if not isinstance(after, dict):
            after = {}

        if resource_type not in approved:
            findings.append(
                _finding(
                    "RESOURCE_TYPE_NOT_APPROVED",
                    "high",
                    address,
                    "Resource type is not approved",
                    f"{resource_type} is not in the configured resource allowlist.",
                    "Use an approved resource type or obtain policy-owner approval.",
                )
            )

        if resource_type == "aws_instance":
            _check_ec2(address, after, findings)

        elif resource_type == "aws_security_group":
            _check_security_group(address, after, findings)

        elif resource_type in {
            "aws_vpc_security_group_ingress_rule",
            "aws_vpc_security_group_egress_rule",
        }:
            _check_standalone_security_rule(
                address,
                resource_type,
                after,
                findings,
            )

        elif resource_type == "aws_iam_policy":
            _check_iam_policy(address, after, findings)

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["severity"], 99),
            item["address"],
            item["rule_id"],
        )
    )

    counts = {
        severity: sum(
            finding["severity"] == severity for finding in findings
        )
        for severity in ("critical", "high", "medium", "low")
    }

    return {
        "policy_version": "initial-2",
        "checks_completed": True,
        "deployment_enabled": False,
        "passed": len(findings) == 0,
        "finding_count": len(findings),
        "severity_counts": counts,
        "findings": findings,
    }
