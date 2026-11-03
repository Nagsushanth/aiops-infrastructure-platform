
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


def _collect_expression_references(value: Any) -> set[str]:
    """Collect Terraform references from configuration expressions."""
    references: set[str] = set()

    if isinstance(value, dict):
        for key, item in value.items():
            if key == "references":
                if not isinstance(item, list) or not all(
                    isinstance(reference, str) for reference in item
                ):
                    raise TerraformPlanParsingError(
                        "Expression references must be a list of strings."
                    )

                references.update(item)
            else:
                references.update(
                    _collect_expression_references(item)
                )

    elif isinstance(value, list):
        for item in value:
            references.update(
                _collect_expression_references(item)
            )

    return references


def _collect_configuration_resources(
    module: dict[str, Any],
) -> dict[str, set[str]]:
    """Collect resource dependencies from Terraform configuration."""

    dependencies: dict[str, set[str]] = {}

    resources = module.get("resources", [])

    if not isinstance(resources, list):
        raise TerraformPlanParsingError(
            "Configuration resources must be a list."
        )

    for resource in resources:
        if not isinstance(resource, dict):
            raise TerraformPlanParsingError(
                "Configuration resource must be an object."
            )

        address = resource.get("address")
        depends_on = resource.get("depends_on", [])

        if not isinstance(address, str) or not address:
            raise TerraformPlanParsingError(
                "Configuration resource address must be a string."
            )

        if not isinstance(depends_on, list) or not all(
            isinstance(item, str) for item in depends_on
        ):
            raise TerraformPlanParsingError(
                "Resource dependencies must be a list of strings."
            )

        expressions = resource.get("expressions", {})

        if not isinstance(expressions, dict):
            raise TerraformPlanParsingError(
                "Resource expressions must be an object."
            )

        references = _collect_expression_references(expressions)

        dependencies[address] = set(depends_on) | references

    # Support nested module calls in Terraform plan configuration.
    module_calls = module.get("module_calls", {})

    if not isinstance(module_calls, dict):
        raise TerraformPlanParsingError(
            "Configuration module_calls must be an object."
        )

    for module_call in module_calls.values():
        if not isinstance(module_call, dict):
            raise TerraformPlanParsingError(
                "Configuration module call must be an object."
            )

        child_module = module_call.get("module")

        if child_module is not None:
            if not isinstance(child_module, dict):
                raise TerraformPlanParsingError(
                    "Configuration child module must be an object."
                )

            child_dependencies = _collect_configuration_resources(
                child_module
            )

            dependencies.update(child_dependencies)

    return dependencies


def _match_resource_reference(
    reference: str,
    resource_addresses: set[str],
) -> str | None:
    """Match an expression reference to a configured resource address."""

    matches = [
        address
        for address in resource_addresses
        if reference == address
        or reference.startswith(address + ".")
        or reference.startswith(address + "[")
    ]

    if not matches:
        return None

    # Prefer the longest address to avoid matching a parent prefix.
    return max(matches, key=len)


def _analyze_dependencies(
    plan: dict[str, Any],
    changed_resources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze Terraform resource dependencies and suggest ordering."""

    configuration = plan.get("configuration", {})

    if not isinstance(configuration, dict):
        raise TerraformPlanParsingError(
            "Terraform configuration must be an object."
        )

    root_module = configuration.get("root_module", {})

    if not isinstance(root_module, dict):
        raise TerraformPlanParsingError(
            "Terraform root_module must be an object."
        )

    raw_dependencies = _collect_configuration_resources(root_module)

    resource_addresses = set(raw_dependencies)

    # Resolve references to actual configured resource addresses.
    dependencies: dict[str, set[str]] = {}

    for address, references in raw_dependencies.items():
        resolved: set[str] = set()

        for reference in references:
            matched = _match_resource_reference(
                reference,
                resource_addresses,
            )

            if matched is not None and matched != address:
                resolved.add(matched)

        dependencies[address] = resolved

    # Build the dependency graph.
    remaining = {
        address: set(required)
        for address, required in dependencies.items()
    }

    ordered: list[str] = []

    # Deterministic topological ordering.
    while remaining:
        ready = sorted(
            address
            for address, required in remaining.items()
            if not required.intersection(remaining)
        )

        if not ready:
            raise TerraformPlanParsingError(
                "Circular dependency detected in Terraform configuration."
            )

        for address in ready:
            ordered.append(address)
            remaining.pop(address)

    changed_actions: dict[str, list[str]] = {
        item["address"]: item.get("actions", [])
        for item in changed_resources
        if isinstance(item.get("address"), str)
    }

    changed_addresses = set(changed_actions)

    create_addresses = {
        address
        for address, actions in changed_actions.items()
        if "create" in actions
    }

    delete_addresses = {
        address
        for address, actions in changed_actions.items()
        if "delete" in actions
    }

    return {
        "dependencies": [
            {
                "resource": address,
                "depends_on": sorted(required),
            }
            for address, required in sorted(dependencies.items())
        ],
        "resource_order": [
            address
            for address in ordered
            if address in changed_addresses
        ],
        "create_order": [
            address
            for address in ordered
            if address in create_addresses
        ],
        "destroy_order": [
            address
            for address in reversed(ordered)
            if address in delete_addresses
        ],
    }


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

    dependency_analysis = _analyze_dependencies(
        plan,
        resources,
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
        "dependency_analysis": dependency_analysis,
    }
