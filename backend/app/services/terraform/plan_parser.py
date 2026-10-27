
"""Parse Terraform plan JSON into a reviewable summary.

This module parses plan data only. It never approves changes or provisions
cloud resources.
"""

from typing import Any


class TerraformPlanParsingError(ValueError):
    """Raised when Terraform plan JSON has an invalid structure."""


def _redact_sensitive(value: Any, sensitive: Any) -> Any:
    """Replace values marked sensitive in Terraform's metadata."""
    if sensitive is True:
        return "[REDACTED]"

    if isinstance(value, dict):
        sensitive_map = sensitive if isinstance(sensitive, dict) else {}
        return {
            key: _redact_sensitive(item, sensitive_map.get(key, False))
            for key, item in value.items()
        }

    if isinstance(value, list):
        sensitive_list = sensitive if isinstance(sensitive, list) else []
        return [
            _redact_sensitive(
                item,
                sensitive_list[index] if index < len(sensitive_list) else False,
            )
            for index, item in enumerate(value)
        ]

    return value


def _classify_actions(actions: list[str]) -> tuple[str, str, bool]:
    """Return action label, impact level and whether review is required."""
    if actions == ["create"]:
        return "create", "medium", True
    if actions == ["update"]:
        return "update", "medium", True
    if actions == ["delete"]:
        return "delete", "high", True
    if actions in (["delete", "create"], ["create", "delete"]):
        return "replace", "high", True
    if actions == ["read"]:
        return "read", "low", False
    if actions == ["no-op"]:
        return "no_op", "low", False

    return "other", "unknown", True


def _changed_attribute_names(before: Any, after: Any) -> list[str]:
    """Identify changed top-level attribute names without returning values."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []

    names = set(before) | set(after)
    return sorted(
        name
        for name in names
        if before.get(name) != after.get(name)
    )


def parse_terraform_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert Terraform show -json output into a reviewable summary."""
    if not isinstance(plan, dict):
        raise TerraformPlanParsingError(
            "Terraform plan must be a JSON object."
        )

    resource_changes = plan.get("resource_changes", [])
    if not isinstance(resource_changes, list):
        raise TerraformPlanParsingError(
            "Terraform plan resource_changes must be a list."
        )

    summary = {
        "create": 0,
        "update": 0,
        "delete": 0,
        "replace": 0,
        "read": 0,
        "no_op": 0,
        "other": 0,
    }

    resources: list[dict[str, Any]] = []
    review_items: list[dict[str, Any]] = []

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

        action_label, impact, requires_review = _classify_actions(actions)
        summary_key = action_label if action_label in summary else "other"
        summary[summary_key] += 1

        before = _redact_sensitive(
            change.get("before"),
            change.get("before_sensitive", False),
        )
        after = _redact_sensitive(
            change.get("after"),
            change.get("after_sensitive", False),
        )

        address = resource.get("address")
        resource_type = resource.get("type")
        resource_name = resource.get("name")

        # Only names are included in the review summary, never attribute values.
        changed_attributes = _changed_attribute_names(
            change.get("before"),
            change.get("after"),
        )

        resource_item = {
            "address": address,
            "mode": resource.get("mode", "managed"),
            "type": resource_type,
            "name": resource_name,
            "provider_name": resource.get("provider_name"),
            "actions": actions,
            "before": before,
            "after": after,
            "after_unknown": change.get("after_unknown", {}),
            "action_reason": resource.get("action_reason"),
        }
        resources.append(resource_item)

        review_items.append(
            {
                "address": address,
                "type": resource_type,
                "name": resource_name,
                "actions": actions,
                "action_label": action_label,
                "impact": impact,
                "changed_attributes": changed_attributes,
                "requires_review": requires_review,
            }
        )

    return {
        "preview_type": "terraform_plan_json",
        "is_real_terraform_plan": False,
        "source_verified": False,
        "deployment_enabled": False,
        "terraform_version": plan.get("terraform_version"),
        "format_version": plan.get("format_version"),
        "resource_summary": {
            **summary,
            "total_resource_changes": len(resources),
        },
        "resources": resources,
        "review_summary": {
            "items": review_items,
            "requires_review_count": sum(
                item["requires_review"] for item in review_items
            ),
            "high_impact_count": sum(
                item["impact"] == "high" for item in review_items
            ),
        },
    }
