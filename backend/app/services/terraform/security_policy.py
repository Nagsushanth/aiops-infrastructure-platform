
"""Read-only security policy checks for Terraform plan JSON."""

import json
from typing import Any


# Initial allowlist for the first implementation.
# Expand only when the corresponding templates and policies are reviewed.
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
            if key in {"cidr_blocks", "ipv6_cidr_blocks"}:
                if any(_is_world_open(cidr) for cidr in _as_list(item)):
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


def _has_wildcard_iam_permission(value: Any) -> bool:
    """Flag IAM policy statements with wildcard actions or resources."""
    value = _decode_policy_document(value)

    if isinstance(value, dict):
        statements = value.get("Statement", [])
        if isinstance(statements, dict):
            statements = [statements]

        for statement in _as_list(statements):
            if not isinstance(statement, dict):
                continue

            action = statement.get("Action", [])
            resource = statement.get("Resource", [])

            actions = [action] if isinstance(action, str) else _as_list(action)
            resources = (
                [resource] if isinstance(resource, str)
                else _as_list(resource)
            )

            if "*" in actions or "*" in resources:
                return True

        return any(
            _has_wildcard_iam_permission(item)
            for key, item in value.items()
            if key != "Statement"
        )

    if isinstance(value, list):
        return any(_has_wildcard_iam_permission(item) for item in value)

    return False


def evaluate_terraform_security(
    plan: dict[str, Any],
    approved_resource_types: set[str] | None = None,
) -> dict[str, Any]:
    """Return policy findings for resources in Terraform plan JSON.

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

        # Deletions do not create a new resource configuration to assess.
        # Replacements and updates are still evaluated.
        if actions == ["delete"] or actions == ["no-op", "delete"]:
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

            metadata = after.get("metadata_options")
            if isinstance(metadata, list) and metadata:
                options = metadata[0]
                if isinstance(options, dict):
                    if options.get("http_tokens") != "required":
                        findings.append(
                            _finding(
                                "EC2_IMDSV2_NOT_REQUIRED",
                                "high",
                                address,
                                "IMDSv2 is not enforced",
                                "Instance metadata tokens are not explicitly required.",
                                "Set metadata_options.http_tokens to required.",
                            )
                        )

            root_devices = _as_list(after.get("root_block_device"))
            for device in root_devices:
                if isinstance(device, dict) and device.get("encrypted") is not True:
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

        if resource_type == "aws_security_group":
            ingress = _as_list(after.get("ingress"))
            for rule in ingress:
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

            egress = _as_list(after.get("egress"))
            for rule in egress:
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

        if resource_type == "aws_vpc_security_group_ingress_rule":
            if _contains_world_open(after):
                findings.append(
                    _finding(
                        "SECURITY_GROUP_PUBLIC_INGRESS",
                        "high",
                        address,
                        "Ingress rule allows public access",
                        "The planned ingress rule includes an unrestricted CIDR.",
                        "Restrict the source CIDR and required ports.",
                    )
                )

        if resource_type == "aws_iam_policy":
            if _has_wildcard_iam_permission(after.get("policy")):
                findings.append(
                    _finding(
                        "IAM_WILDCARD_PERMISSION",
                        "high",
                        address,
                        "IAM policy contains wildcard permissions",
                        "The policy contains a wildcard Action or Resource.",
                        "Apply least privilege by limiting actions and resources.",
                    )
                )

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
        "policy_version": "initial-1",
        "checks_completed": True,
        "deployment_enabled": False,
        "passed": len(findings) == 0,
        "finding_count": len(findings),
        "severity_counts": counts,
        "findings": findings,
    }
