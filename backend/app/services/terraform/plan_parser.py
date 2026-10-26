
"""Parse Terraform plan JSON into a reviewable summary.

This module only parses plan data. It never executes Terraform,
approves changes, or provisions cloud resources.
"""

from typing import Any


class TerraformPlanParsingError(ValueError):
    """Raised when Terraform plan JSON has an invalid structure."""


def _redact_sensitive(
    value: Any,
    sensitive: Any,
) -> Any:
    """Replace values marked sensitive in Terraform's sensitivity metadata."""
    if sensitive is True:
        return "[REDACTED]"

    if isinstance(value, dict):
        if not isinstance(sensitive, dict):
            sensitive = {}

        return {
            key: _redact_sensitive(item, sensitive.get(key, False))
            for key, item in value.items()
        }

    if isinstance(value, list):
        if not isinstance(sensitive, list):
            sensitive = []

        return [
            _redact_sensitive(
                item,
                sensitive[index] if index < len(sensitive) else False,
            )
            for index, item in enumerate(value)
        ]

    return value


def parse_terraform_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert Terraform's `terraform show -json` output into a summary."""
    if not isinstance(plan, dict):
        raise TerraformPlanParsingError(
            "Terraform plan must be a JSON object."
        )

    resource_changes = plan.get("resource_changes", [])
    if not isinstance(resource_changes, list):
        raise TerraformPlanParsingError(
            "Terraform plan resource_changes must be a list."
        )

    resources: list[dict[str, Any]] = []
    summary = {
        "create": 0,
        "update": 0,
        "delete": 0,
        "replace": 0,
        "read": 0,
        "no_op": 0,
        "other": 0,
    }

    for resource in resource_changes:
        if not isinstance(resource, dict):
            raise TerraformPlanParsingError(
                "Each resource change must be a JSON object."
            )

        change = resource.get("change", {})
        if not isinstance(change, dict):
            raise TerraformPlanParsingError(
                "Each resource change must contain a change object."
            )

        actions = change.get("actions", [])
        if not isinstance(actions, list) or not all(
            isinstance(action, str) for action in actions
        ):
            raise TerraformPlanParsingError(
                "Resource actions must be a list of strings."
            )

        if actions == ["create"]:
            summary["create"] += 1
        elif actions == ["update"]:
            summary["update"] += 1
        elif actions == ["delete"]:
            summary["delete"] += 1
        elif actions in (["delete", "create"], ["create", "delete"]):
            summary["replace"] += 1
        elif actions == ["read"]:
            summary["read"] += 1
        elif actions == ["no-op"]:
            summary["no_op"] += 1
        else:
            summary["other"] += 1

        before = _redact_sensitive(
            change.get("before"),
            change.get("before_sensitive", False),
        )
        after = _redact_sensitive(
            change.get("after"),
            change.get("after_sensitive", False),
        )

        resources.append(
            {
                "address": resource.get("address"),
                "mode": resource.get("mode", "managed"),
                "type": resource.get("type"),
                "name": resource.get("name"),
                "provider_name": resource.get("provider_name"),
                "actions": actions,
                "before": before,
                "after": after,
                "after_unknown": change.get("after_unknown", {}),
                "action_reason": resource.get("action_reason"),
            }
        )

    return {
        "preview_type": "terraform_plan_json",
        "is_real_terraform_plan": True,
        "deployment_enabled": False,
        "terraform_version": plan.get("terraform_version"),
        "format_version": plan.get("format_version"),
        "resource_summary": {
            **summary,
            "total_resource_changes": len(resources),
        },
        "resources": resources,
    }
