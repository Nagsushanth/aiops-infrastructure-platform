
import pytest

from backend.app.services.terraform.security_policy import (
    evaluate_terraform_security,
)


def make_resource(address, resource_type, after, actions=None):
    return {
        "address": address,
        "type": resource_type,
        "change": {
            "actions": actions or ["create"],
            "before": None,
            "after": after,
        },
    }


def make_plan(*resources):
    return {"resource_changes": list(resources)}


def test_flags_public_ec2_and_missing_imdsv2():
    plan = make_plan(
        make_resource(
            "aws_instance.example",
            "aws_instance",
            {
                "associate_public_ip_address": True,
                "metadata_options": [{"http_tokens": "optional"}],
                "root_block_device": [{"encrypted": True}],
            },
        )
    )

    result = evaluate_terraform_security(plan)
    rule_ids = {item["rule_id"] for item in result["findings"]}

    assert result["passed"] is False
    assert "EC2_PUBLIC_IP" in rule_ids
    assert "EC2_IMDSV2_NOT_REQUIRED" in rule_ids
    assert "EBS_ROOT_NOT_ENCRYPTED" not in rule_ids


def test_flags_public_security_group_ingress():
    plan = make_plan(
        make_resource(
            "aws_security_group.example",
            "aws_security_group",
            {
                "ingress": [
                    {
                        "from_port": 22,
                        "to_port": 22,
                        "protocol": "tcp",
                        "cidr_blocks": ["0.0.0.0/0"],
                    }
                ],
                "egress": [],
            },
        )
    )

    result = evaluate_terraform_security(plan)

    assert any(
        item["rule_id"] == "SECURITY_GROUP_PUBLIC_INGRESS"
        for item in result["findings"]
    )


def test_flags_unencrypted_root_volume():
    plan = make_plan(
        make_resource(
            "aws_instance.example",
            "aws_instance",
            {
                "associate_public_ip_address": False,
                "metadata_options": [{"http_tokens": "required"}],
                "root_block_device": [{"encrypted": False}],
            },
        )
    )

    result = evaluate_terraform_security(plan)

    assert any(
        item["rule_id"] == "EBS_ROOT_NOT_ENCRYPTED"
        for item in result["findings"]
    )


def test_flags_unapproved_resource_type():
    plan = make_plan(
        make_resource(
            "aws_s3_bucket.example",
            "aws_s3_bucket",
            {"bucket": "example"},
        )
    )

    result = evaluate_terraform_security(plan)

    assert any(
        item["rule_id"] == "RESOURCE_TYPE_NOT_APPROVED"
        for item in result["findings"]
    )


def test_clean_ec2_configuration_has_no_findings():
    plan = make_plan(
        make_resource(
            "aws_instance.example",
            "aws_instance",
            {
                "associate_public_ip_address": False,
                "metadata_options": [{"http_tokens": "required"}],
                "root_block_device": [{"encrypted": True}],
            },
        )
    )

    result = evaluate_terraform_security(plan)

    assert result["passed"] is True
    assert result["finding_count"] == 0
    assert result["deployment_enabled"] is False


@pytest.mark.parametrize("bad_plan", [None, [], "invalid"])
def test_rejects_invalid_plan_input(bad_plan):
    with pytest.raises(ValueError):
        evaluate_terraform_security(bad_plan)
