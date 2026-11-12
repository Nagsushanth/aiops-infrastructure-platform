
import pytest

from backend.app.services.terraform.plan_parser import (
    TerraformPlanParsingError,
    parse_terraform_plan,
)


def sample_plan():
    return {
        "format_version": "1.2",
        "terraform_version": "1.15.6",
        "resource_changes": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "provider_name": "registry.terraform.io/hashicorp/aws",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {
                        "instance_type": "c6i.xlarge",
                        "password": "example-secret",
                    },
                    "after_sensitive": {
                        "password": True,
                    },
                    "after_unknown": {
                        "id": True,
                    },
                },
            },
            {
                "address": "aws_security_group.instance",
                "mode": "managed",
                "type": "aws_security_group",
                "name": "instance",
                "provider_name": "registry.terraform.io/hashicorp/aws",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {"name": "aiops-ec2"},
                },
            },
        ],
    }


def test_parses_realistic_resource_changes():
    result = parse_terraform_plan(sample_plan())

    assert result["preview_type"] == "terraform_plan_json"
    assert result["is_real_terraform_plan"] is False
    assert result["source_verified"] is False
    assert result["deployment_enabled"] is False
    assert result["resource_summary"]["create"] == 2
    assert result["resource_summary"]["total_resource_changes"] == 2
    assert len(result["resources"]) == 2


def test_preserves_resource_actions_and_attributes():
    result = parse_terraform_plan(sample_plan())

    instance = result["resources"][0]

    assert instance["address"] == "aws_instance.this"
    assert instance["actions"] == ["create"]
    assert instance["after"]["instance_type"] == "c6i.xlarge"
    assert instance["after_unknown"]["id"] is True


def test_redacts_sensitive_values():
    result = parse_terraform_plan(sample_plan())

    instance = result["resources"][0]

    assert instance["after"]["password"] == "[REDACTED]"
    assert "example-secret" not in str(result)


@pytest.mark.parametrize(
    ("actions", "summary_key"),
    [
        (["create"], "create"),
        (["update"], "update"),
        (["delete"], "delete"),
        (["delete", "create"], "replace"),
        (["create", "delete"], "replace"),
        (["read"], "read"),
        (["no-op"], "no_op"),
    ],
)
def test_summarizes_action_types(actions, summary_key):
    plan = {
        "resource_changes": [
            {
                "address": "aws_instance.example",
                "type": "aws_instance",
                "name": "example",
                "change": {
                    "actions": actions,
                },
            }
        ]
    }

    result = parse_terraform_plan(plan)

    assert result["resource_summary"][summary_key] == 1


def test_rejects_non_object_plan():
    with pytest.raises(TerraformPlanParsingError):
        parse_terraform_plan([])


def test_rejects_invalid_resource_changes():
    with pytest.raises(TerraformPlanParsingError):
        parse_terraform_plan({"resource_changes": {}})


def test_rejects_invalid_action_structure():
    plan = {
        "resource_changes": [
            {
                "address": "aws_instance.example",
                "change": {"actions": "create"},
            }
        ]
    }

    with pytest.raises(TerraformPlanParsingError):
        parse_terraform_plan(plan)


def test_handles_empty_plan():
    result = parse_terraform_plan({"resource_changes": []})

    assert result["resources"] == []
    assert result["resource_summary"]["total_resource_changes"] == 0
    assert result["deployment_enabled"] is False


def test_extracts_resource_dependencies_and_creation_order():
    plan = {
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_instance.app",
                        "depends_on": [
                            "aws_security_group.app"
                        ],
                    },
                    {
                        "address": "aws_security_group.app",
                        "depends_on": [],
                    },
                ]
            }
        },
        "resource_changes": [
            {
                "address": "aws_instance.app",
                "type": "aws_instance",
                "name": "app",
                "change": {"actions": ["create"]},
            },
            {
                "address": "aws_security_group.app",
                "type": "aws_security_group",
                "name": "app",
                "change": {"actions": ["create"]},
            },
        ],
    }

    result = parse_terraform_plan(plan)
    analysis = result["dependency_analysis"]

    assert analysis["dependencies"] == [
        {
            "resource": "aws_instance.app",
            "depends_on": ["aws_security_group.app"],
        },
        {
            "resource": "aws_security_group.app",
            "depends_on": [],
        },
    ]

    assert analysis["create_order"] == [
        "aws_security_group.app",
        "aws_instance.app",
    ]


def test_destroy_order_reverses_dependency_order():
    plan = {
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_instance.app",
                        "depends_on": ["aws_security_group.app"],
                    },
                    {
                        "address": "aws_security_group.app",
                        "depends_on": [],
                    },
                ]
            }
        },
        "resource_changes": [
            {
                "address": "aws_instance.app",
                "type": "aws_instance",
                "name": "app",
                "change": {"actions": ["delete"]},
            },
            {
                "address": "aws_security_group.app",
                "type": "aws_security_group",
                "name": "app",
                "change": {"actions": ["delete"]},
            },
        ],
    }

    result = parse_terraform_plan(plan)

    assert result["dependency_analysis"]["destroy_order"] == [
        "aws_instance.app",
        "aws_security_group.app",
    ]


def test_handles_plan_without_configuration():
    result = parse_terraform_plan({
        "resource_changes": []
    })

    assert result["dependency_analysis"] == {
        "dependencies": [],
        "resource_order": [],
        "create_order": [],
        "destroy_order": [],
    }


def test_detects_circular_dependencies():
    plan = {
        "configuration": {
            "root_module": {
                "resources": [
                    {
                        "address": "aws_instance.first",
                        "depends_on": ["aws_instance.second"],
                    },
                    {
                        "address": "aws_instance.second",
                        "depends_on": ["aws_instance.first"],
                    },
                ]
            }
        },
        "resource_changes": [],
    }

    with pytest.raises(TerraformPlanParsingError):
        parse_terraform_plan(plan)

@pytest.mark.parametrize(
    "address",
    [
        None,
        "",
        "   ",
        123,
        [],
        {},
    ],
)
def test_rejects_invalid_resource_address(address):
    plan = {
        "resource_changes": [
            {
                "address": address,
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": {},
                },
            }
        ]
    }

    with pytest.raises(TerraformPlanParsingError):
        parse_terraform_plan(plan)