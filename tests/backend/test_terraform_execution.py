
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
    execute_terraform_plan,
)


@pytest.fixture
def specification():
    return ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="ec2",
        environment="development",
        region="us-east-1",
        vcpus=2,
        memory_gb=2,
        public_access=False,
        operating_system="amazon linux 2023",
    )


def completed_process(stdout="", returncode=0):
    return SimpleNamespace(
        stdout=stdout,
        returncode=returncode,
    )


def fake_run_arguments(
    command,
    *,
    cwd,
    timeout=None,
    capture_output=False,
    text=False,
    check=False,
    shell=False,
    env=None,
    **kwargs,
):
    """Accept the same keyword arguments as subprocess.run()."""
    return None


def test_rejects_invalid_timeout(specification):
    with pytest.raises(ValueError):
        execute_terraform_plan(
            specification,
            timeout_seconds=0,
        )


def test_rejects_unsupported_specification():
    unsupported = ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="ec2",
        environment="development",
        region="us-east-1",
        vcpus=3,
        memory_gb=3,
        public_access=False,
    )

    with pytest.raises(TerraformExecutionError) as exc:
        execute_terraform_plan(unsupported)

    assert exc.value.code == "UNSUPPORTED_SPECIFICATION"


def test_runs_plan_without_apply(specification):
    commands = []

    def fake_run(
        command,
        *,
        cwd,
        timeout=None,
        capture_output=False,
        text=False,
        check=False,
        shell=False,
        env=None,
        **kwargs,
    ):
        commands.append(command)

        if command[1] == "plan":
            (Path(cwd) / "tfplan").write_bytes(
                b"mock-binary-plan"
            )
            return completed_process()

        if command[1] == "show":
            plan_json = {
                "terraform_version": "1.15.6",
                "format_version": "1.2",
                "resource_changes": [
                    {
                        "address": "aws_instance.this",
                        "mode": "managed",
                        "type": "aws_instance",
                        "name": "this",
                        "change": {
                            "actions": ["create"],
                            "before": None,
                            "after": {
                                "instance_type": "t3.small",
                                "private_value": (
                                    "must-not-be-returned"
                                ),
                            },
                            "after_sensitive": {},
                        },
                    }
                ],
            }
            return completed_process(
                stdout=json.dumps(plan_json)
            )

        return completed_process()

    with patch(
        "backend.app.services.terraform.execution.subprocess.run",
        side_effect=fake_run,
    ):
        result = execute_terraform_plan(specification)

    assert [command[1] for command in commands] == [
        "init",
        "plan",
        "show",
    ]

    assert all(
        "apply" not in command
        for command in commands
    )

    assert result["status"] == "succeeded"
    assert result["is_real_terraform_plan"] is True
    assert result["source_verified"] is False
    assert result["deployment_enabled"] is False

    assert len(result["plan_fingerprint"]) == 64
    assert len(result["specification_fingerprint"]) == 64

    serialized_summary = json.dumps(
        result["plan_summary"]
    )

    assert "must-not-be-returned" not in serialized_summary
    assert (
        result["plan_summary"]["resource_summary"]["create"]
        == 1
    )


def test_command_failure_does_not_expose_raw_output(
    specification,
):
    def fake_run(
        command,
        *,
        cwd,
        timeout=None,
        capture_output=False,
        text=False,
        check=False,
        shell=False,
        env=None,
        **kwargs,
    ):
        return completed_process(
            stdout="sensitive-diagnostic",
            returncode=1,
        )

    with patch(
        "backend.app.services.terraform.execution.subprocess.run",
        side_effect=fake_run,
    ):
        with pytest.raises(TerraformExecutionError) as exc:
            execute_terraform_plan(specification)

    assert exc.value.code == "COMMAND_FAILED"
    assert "sensitive-diagnostic" not in str(exc.value)


def test_timeout_is_reported_safely(specification):
    def fake_run(
        command,
        *,
        cwd,
        timeout=None,
        capture_output=False,
        text=False,
        check=False,
        shell=False,
        env=None,
        **kwargs,
    ):
        raise subprocess.TimeoutExpired(
            command,
            timeout,
        )

    with patch(
        "backend.app.services.terraform.execution.subprocess.run",
        side_effect=fake_run,
    ):
        with pytest.raises(TerraformExecutionError) as exc:
            execute_terraform_plan(specification)

    assert exc.value.code == "COMMAND_TIMEOUT"
