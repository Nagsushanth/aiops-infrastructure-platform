
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import backend.app.services.terraform.execution as execution_module
import pytest
import os
import signal
import sys
import time
import subprocess

from backend.app.services.terraform.artifacts import (
    load_plan_artifact,
    save_plan_artifact,
    save_provider_lock_artifact,
    load_provider_lock_artifact,
    TerraformArtifactError,
    save_configuration_artifact,
    load_configuration_artifact,
    delete_configuration_artifact,
)
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.execution import (
    TerraformExecutionError,
    execute_terraform_plan,
    reconstructed_terraform_configuration_workspace,
    reconstructed_terraform_workspace,
    initialize_terraform_apply_workspace,
    build_terraform_environment,
    execute_terraform_destroy_plan,
)


@pytest.fixture(autouse=True)
def isolate_terraform_artifacts(tmp_path, monkeypatch):
    """Keep test-generated Terraform artifacts inside pytest's temp directory."""
    artifact_dir = tmp_path / "terraform-artifacts"

    monkeypatch.setenv(
        "AIOPS_TERRAFORM_ARTIFACT_DIR",
        str(artifact_dir),
    )
    monkeypatch.setenv("TF_STATE_BUCKET", "test-terraform-state")
    monkeypatch.setenv("TF_STATE_REGION", "ap-south-1")
    monkeypatch.setenv("TF_STATE_PREFIX", "test/requests")


@pytest.fixture
def specification():
    return ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="ec2",
        environment="development",
        region="ap-south-1",
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
            infrastructure_request_id=123,
            timeout_seconds=0,
        )


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        True,
        False,
        1.5,
        "180",
        -1,
        601,
    ],
)
def test_rejects_invalid_timeout_types_and_ranges(
    specification,
    invalid_timeout,
):
    with pytest.raises(ValueError):
        execute_terraform_plan(
            specification,
            infrastructure_request_id=123,
            timeout_seconds=invalid_timeout,
        )


def test_rejects_missing_terraform_backend_injection_target(
    specification,
    monkeypatch,
):
    monkeypatch.setattr(
        execution_module,
        "generate_terraform",
        lambda specification: 'provider "aws" {}',
    )

    with pytest.raises(TerraformExecutionError) as exc:
        execute_terraform_plan(
            specification,
            infrastructure_request_id=123,
        )

    assert exc.value.code == "BACKEND_BLOCK_INJECTION_FAILED"


def test_inject_s3_backend_adds_verified_configuration():
    configuration = 'terraform {\n  required_version = ">= 1.10.0"\n}\n'

    backend_config = {
        "bucket": "test-terraform-state",
        "key": "test/requests/123/terraform.tfstate",
        "region": "ap-south-1",
    }

    result = execution_module._inject_s3_backend(
        configuration,
        backend_config,
    )

    assert result.count('backend "s3"') == 1
    assert 'bucket       = "test-terraform-state"' in result
    assert 'key          = "test/requests/123/terraform.tfstate"' in result
    assert "encrypt      = true" in result
    assert "use_lockfile = true" in result


def test_inject_s3_backend_rejects_existing_backend():
    configuration = (
        'terraform {\n'
        '  backend "local" {}\n'
        '}\n'
    )

    backend_config = {
        "bucket": "test-terraform-state",
        "key": "test/requests/123/terraform.tfstate",
        "region": "ap-south-1",
    }

    with pytest.raises(TerraformExecutionError) as exc:
        execution_module._inject_s3_backend(
            configuration,
            backend_config,
        )

    assert exc.value.code == "BACKEND_BLOCK_ALREADY_CONFIGURED"


def test_rejects_unsupported_specification():
    unsupported = ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="ec2",
        environment="development",
        region="ap-south-1",
        vcpus=3,
        memory_gb=3,
        public_access=False,
    )

    with pytest.raises(TerraformExecutionError) as exc:
        execute_terraform_plan(
            unsupported,
            infrastructure_request_id=123,
        )

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

        if command[1] == "init":
            terraform_config = (
                Path(cwd) / "main.tf"
            ).read_text(encoding="utf-8")

            assert 'backend "s3"' in terraform_config
            assert 'bucket       = "test-terraform-state"' in terraform_config
            assert 'key          = "test/requests/123/terraform.tfstate"' in terraform_config
            assert 'region       = "ap-south-1"' in terraform_config
            assert "use_lockfile = true" in terraform_config
            assert "-backend=false" not in command

            # Simulate the provider lock file created by terraform init.
            (Path(cwd) / ".terraform.lock.hcl").write_text(
                '# Test provider lock file\n',
                encoding="utf-8",
            )

            return completed_process()

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
        result = execute_terraform_plan(
            specification,
            infrastructure_request_id=123,
        )

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

    assert result["artifact_path"]
    assert result["artifact_sha256"] == result["plan_fingerprint"]

    assert result["provider_lock_path"]
    assert len(result["provider_lock_sha256"]) == 64

    saved_lock = load_provider_lock_artifact(
        result["provider_lock_path"],
        result["provider_lock_sha256"],
    )

    assert saved_lock == b'# Test provider lock file\n'

    saved_plan = load_plan_artifact(
        result["artifact_path"],
        result["artifact_sha256"],
    )

    assert saved_plan == b"mock-binary-plan"

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
            execute_terraform_plan(
                specification,
                infrastructure_request_id=123,
            )

    assert exc.value.code == "INIT_FAILED_GENERAL"
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
            execute_terraform_plan(
                specification,
                infrastructure_request_id=123,
            )

    assert exc.value.code == "INIT_TIMEOUT"


def test_plan_includes_unavailable_cost_estimate(specification):
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

        if command[1] == "init":
            # Simulate terraform init generating its provider lock file.
            (Path(cwd) / ".terraform.lock.hcl").write_text(
                '# Test provider lock file\n',
                encoding="utf-8",
            )
            return completed_process()

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
                                "instance_type": "t3.small"
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

    with (
        patch(
            "backend.app.services.terraform.execution.subprocess.run",
            side_effect=fake_run,
        ),
        patch.object(
            execution_module,
            "get_ec2_pricing_catalog",
            return_value={},
        ),
        patch.object(
            execution_module,
            "BUDGET_THRESHOLD_USD",
            None,
        ),
    ):
        result = execute_terraform_plan(
            specification,
            infrastructure_request_id=123,
        )

    estimate = result["plan_summary"]["cost_estimate"]

    assert estimate["status"] == "unavailable"
    assert estimate["instance_type"] == "t3.small"
    assert estimate["region"] == "ap-south-1"
    assert estimate["estimated_monthly_compute_cost"] is None
    assert estimate["budget_status"] == "not_evaluated"


def test_plan_includes_verified_cost_estimate(specification):
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

        if command[1] == "init":
            # Simulate terraform init generating its provider lock file.
            (Path(cwd) / ".terraform.lock.hcl").write_text(
                '# Test provider lock file\n',
                encoding="utf-8",
            )
            return completed_process()

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
                                "instance_type": "t3.small"
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

    test_catalog = {
        "ap-south-1": {
            "t3.small": {
                "hourly_usd": "0.0208",
                "source": "Test fixture only",
                "verified_on": "2026-10-30",
            }
        }
    }

    with (
        patch(
            "backend.app.services.terraform.execution.subprocess.run",
            side_effect=fake_run,
        ),
        patch.object(
            execution_module,
            "get_ec2_pricing_catalog",
            return_value=test_catalog,
        ),
        patch.object(
            execution_module,
            "BUDGET_THRESHOLD_USD",
            "20",
        ),
    ):
        result = execute_terraform_plan(
            specification,
            infrastructure_request_id=123,
        )

    estimate = result["plan_summary"]["cost_estimate"]

    assert estimate["status"] == "estimated"
    assert estimate["estimated_monthly_compute_cost"] == "15.18"
    assert estimate["budget_status"] == "within_threshold"
    assert estimate["pricing_source"] == "Test fixture only"
    assert estimate["pricing_verified_on"] == "2026-10-30"
    assert estimate["assumptions"]["includes"] == ["EC2 compute only"]


def test_provider_lock_artifact_save_and_load():
    lock_content = b"""
provider "registry.terraform.io/hashicorp/aws" {
  version = "5.100.0"
}
"""

    path, checksum = save_provider_lock_artifact(lock_content)

    loaded = load_provider_lock_artifact(path, checksum)

    assert loaded == lock_content
    assert len(checksum) == 64


def test_provider_lock_artifact_rejects_checksum_mismatch():
    lock_content = b"provider lock content"

    path, checksum = save_provider_lock_artifact(lock_content)

    with pytest.raises(TerraformArtifactError) as exc:
        load_provider_lock_artifact(path, "0" * 64)

    assert str(exc.value) == "LOCK_ARTIFACT_CHECKSUM_MISMATCH"


def test_provider_lock_artifact_rejects_invalid_extension():
    plan_content = b"not a provider lock file"
    path, checksum = save_provider_lock_artifact(plan_content)

    # Rename the artifact to a different extension for this test.
    invalid_path = str(Path(path).with_suffix(".tfplan"))
    Path(path).rename(invalid_path)

    with pytest.raises(TerraformArtifactError) as exc:
        load_provider_lock_artifact(invalid_path, checksum)

    assert str(exc.value) == "LOCK_ARTIFACT_PATH_INVALID"


def test_controlled_process_captures_output():
    result = execution_module._run_controlled_process(
        [sys.executable, "-c", "print('controlled-success')"],
        cwd=Path.cwd(),
        timeout_seconds=5,
        env=dict(__import__("os").environ),
        lease_renewal_callback=lambda: True,
        lease_renewal_interval_seconds=1,
    )

    assert result.returncode == 0
    assert "controlled-success" in result.stdout


def test_controlled_process_terminates_on_timeout():
    with pytest.raises(TerraformExecutionError) as exc:
        execution_module._run_controlled_process(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(10)",
            ],
            cwd=Path.cwd(),
            timeout_seconds=1,
            env=dict(__import__("os").environ),
            lease_renewal_callback=lambda: True,
            lease_renewal_interval_seconds=0.1,
        )

    assert exc.value.code == "PROCESS_TIMEOUT"


def test_controlled_process_terminates_when_lease_is_lost():
    with pytest.raises(TerraformExecutionError) as exc:
        execution_module._run_controlled_process(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(10)",
            ],
            cwd=Path.cwd(),
            timeout_seconds=5,
            env=dict(__import__("os").environ),
            lease_renewal_callback=lambda: False,
            lease_renewal_interval_seconds=0.1,
        )

    assert exc.value.code == "WORKER_LEASE_LOST"

def test_controlled_process_rejects_lost_lease_after_process_exits():
    callback_calls = []

    def lease_callback():
        callback_calls.append(True)
        return False

    with pytest.raises(TerraformExecutionError) as exc:
        execution_module._run_controlled_process(
            [sys.executable, "-c", "print('process-finished')"],
            cwd=Path.cwd(),
            timeout_seconds=5,
            env=dict(os.environ),
            lease_renewal_callback=lease_callback,
            lease_renewal_interval_seconds=10,
        )

    assert exc.value.code == "WORKER_LEASE_LOST"
    assert len(callback_calls) == 1


def test_controlled_process_handles_lease_callback_exception():
    def lease_callback():
        raise RuntimeError("database connection failed")

    with pytest.raises(TerraformExecutionError) as exc:
        execution_module._run_controlled_process(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(10)",
            ],
            cwd=Path.cwd(),
            timeout_seconds=5,
            env=dict(os.environ),
            lease_renewal_callback=lease_callback,
            lease_renewal_interval_seconds=0.1,
        )

    assert exc.value.code == "WORKER_LEASE_RENEWAL_FAILED"

def test_terminate_process_group_kills_child_after_parent_exits(tmp_path):
    marker_file = tmp_path / "child_survived.txt"

    child_code = (
        "import time; "
        "from pathlib import Path; "
        "time.sleep(1); "
        f"Path({str(marker_file)!r}).write_text('child survived')"
    )

    parent_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    )

    parent = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    try:
        parent.wait(timeout=5)

        assert parent.poll() is not None

        execution_module._terminate_process_group(
            parent,
            grace_seconds=0.2,
        )

        time.sleep(1.2)

        assert not marker_file.exists()

    finally:
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_configuration_artifact_save_and_load():
    configuration = """
terraform {
  required_version = ">= 1.10.0"
}
"""

    path, checksum = save_configuration_artifact(configuration)

    loaded = load_configuration_artifact(path, checksum)

    assert loaded == configuration
    assert len(checksum) == 64

    delete_configuration_artifact(path)


def test_configuration_artifact_rejects_checksum_mismatch():
    configuration = 'resource "test_resource" "example" {}'

    path, checksum = save_configuration_artifact(configuration)

    with pytest.raises(TerraformArtifactError) as exc:
        load_configuration_artifact(path, "0" * 64)

    assert str(exc.value) == "CONFIGURATION_ARTIFACT_CHECKSUM_MISMATCH"

    delete_configuration_artifact(path)


def test_configuration_artifact_rejects_invalid_extension():
    configuration = 'resource "test_resource" "example" {}'

    path, checksum = save_configuration_artifact(configuration)

    invalid_path = str(Path(path).with_suffix(".txt"))
    Path(path).rename(invalid_path)

    with pytest.raises(TerraformArtifactError) as exc:
        load_configuration_artifact(invalid_path, checksum)

    assert str(exc.value) == "CONFIGURATION_ARTIFACT_PATH_INVALID"

    Path(invalid_path).unlink()

def test_reconstructed_workspace_contains_verified_artifacts(tmp_path):
    configuration = """
terraform {
  required_version = ">= 1.10.0"
}
"""
    provider_lock = b'# Test provider lock file\n'
    plan_bytes = b'valid-test-terraform-plan'

    configuration_path, configuration_checksum = (
        save_configuration_artifact(configuration)
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        provider_lock
    )
    plan_path, plan_checksum = save_plan_artifact(plan_bytes)

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with reconstructed_terraform_workspace(
        configuration_artifact_path=configuration_path,
        configuration_artifact_sha256=configuration_checksum,
        provider_lock_path=lock_path,
        provider_lock_sha256=lock_checksum,
        plan_artifact_path=plan_path,
        plan_artifact_sha256=plan_checksum,
        temp_root=workspace_root,
    ) as workspace:
        assert (workspace / "main.tf").read_text() == configuration
        assert (workspace / ".terraform.lock.hcl").read_bytes() == provider_lock
        assert (workspace / "tfplan").read_bytes() == plan_bytes

        assert (workspace / "main.tf").stat().st_mode & 0o777 == 0o600
        assert (workspace / "tfplan").stat().st_mode & 0o777 == 0o600

        workspace_path = workspace

    assert not workspace_path.exists()


def test_reconstructed_configuration_workspace_excludes_old_plan(
    tmp_path,
):
    configuration = (
        'terraform { required_version = ">= 1.10.0" }'
    )
    provider_lock = b"# Test provider lock file\n"

    configuration_path, configuration_checksum = (
        save_configuration_artifact(configuration)
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        provider_lock
    )

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with reconstructed_terraform_configuration_workspace(
        configuration_artifact_path=configuration_path,
        configuration_artifact_sha256=configuration_checksum,
        provider_lock_path=lock_path,
        provider_lock_sha256=lock_checksum,
        temp_root=workspace_root,
    ) as workspace:
        assert (workspace / "main.tf").read_text() == configuration
        assert (
            workspace / ".terraform.lock.hcl"
        ).read_bytes() == provider_lock

        assert not (workspace / "tfplan").exists()

        assert (
            (workspace / "main.tf").stat().st_mode & 0o777
        ) == 0o600

        assert (
            (workspace / ".terraform.lock.hcl").stat().st_mode & 0o777
        ) == 0o600

        workspace_path = workspace

    assert not workspace_path.exists()

def test_reconstructed_workspace_rejects_invalid_checksum(tmp_path):
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform { required_version = ">= 1.10.0" }'
        )
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        b'# Test provider lock file\n'
    )
    plan_path, plan_checksum = save_plan_artifact(
        b'valid-test-terraform-plan'
    )

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with pytest.raises(TerraformExecutionError) as exc:
        with reconstructed_terraform_workspace(
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256="0" * 64,
            provider_lock_path=lock_path,
            provider_lock_sha256=lock_checksum,
            plan_artifact_path=plan_path,
            plan_artifact_sha256=plan_checksum,
            temp_root=workspace_root,
        ):
            pass

    assert exc.value.code == "WORKSPACE_ARTIFACT_VALIDATION_FAILED"
    assert list(workspace_root.iterdir()) == []


def test_reconstructed_workspace_cleans_up_after_exception(tmp_path):
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform { required_version = ">= 1.10.0" }'
        )
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        b'# Test provider lock file\n'
    )
    plan_path, plan_checksum = save_plan_artifact(
        b'valid-test-terraform-plan'
    )

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    workspace_path = None

    with pytest.raises(RuntimeError, match="simulated failure"):
        with reconstructed_terraform_workspace(
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256=configuration_checksum,
            provider_lock_path=lock_path,
            provider_lock_sha256=lock_checksum,
            plan_artifact_path=plan_path,
            plan_artifact_sha256=plan_checksum,
            temp_root=workspace_root,
        ) as workspace:
            workspace_path = workspace
            raise RuntimeError("simulated failure")

    assert workspace_path is not None
    assert not workspace_path.exists()
    assert list(workspace_root.iterdir()) == []


def test_initialize_apply_workspace_uses_readonly_lockfile(
    tmp_path,
    monkeypatch,
):
    from backend.app.services.terraform import execution

    captured = {}

    def fake_run_controlled_process(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="Terraform initialized",
            stderr="",
        )

    monkeypatch.setattr(
        execution,
        "_run_controlled_process",
        fake_run_controlled_process,
    )

    callback = lambda: True
    env = {"PATH": "/usr/bin"}

    result = initialize_terraform_apply_workspace(
        tmp_path,
        timeout_seconds=120,
        env=env,
        lease_renewal_callback=callback,
    )

    assert result.returncode == 0
    assert captured["command"] == [
        "terraform",
        "init",
        "-input=false",
        "-no-color",
        "-lockfile=readonly",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["timeout_seconds"] == 120
    assert captured["env"] == env
    assert captured["lease_renewal_callback"] is callback


def test_initialize_apply_workspace_propagates_execution_error(
    tmp_path,
    monkeypatch,
):
    from backend.app.services.terraform import execution

    def fake_run_controlled_process(command, **kwargs):
        raise TerraformExecutionError("PROCESS_TIMEOUT")

    monkeypatch.setattr(
        execution,
        "_run_controlled_process",
        fake_run_controlled_process,
    )

    with pytest.raises(TerraformExecutionError) as exc:
        initialize_terraform_apply_workspace(
            tmp_path,
            timeout_seconds=120,
            env={"PATH": "/usr/bin"},
            lease_renewal_callback=lambda: True,
        )

    assert exc.value.code == "PROCESS_TIMEOUT"


def test_build_terraform_environment_uses_restricted_allowlist(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_PROFILE", "aiops-training")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-included")
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    env, temp_root = build_terraform_environment()

    assert env["PATH"] == "/usr/bin"
    assert env["AWS_PROFILE"] == "aiops-training"
    assert env["AWS_SECRET_ACCESS_KEY"] == "test-secret"
    assert "UNRELATED_SECRET" not in env

    assert env["TF_IN_AUTOMATION"] == "1"
    assert env["TF_INPUT"] == "0"

    assert temp_root == tmp_path
    assert env["TMPDIR"] == str(tmp_path)
    assert env["TEMP"] == str(tmp_path)
    assert env["TMP"] == str(tmp_path)


def test_build_terraform_environment_uses_default_temp_directory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setattr(
        "backend.app.services.terraform.execution.tempfile.gettempdir",
        lambda: str(tmp_path),
    )

    env, temp_root = build_terraform_environment()

    assert temp_root == tmp_path
    assert env["TMPDIR"] == str(tmp_path)
    assert env["TF_INPUT"] == "0"


def test_generates_destroy_plan_without_executing_destroy(
    monkeypatch,
):
    configuration = (
        'terraform { required_version = ">= 1.10.0" }'
    )
    provider_lock = b"# Test provider lock file\n"

    configuration_path, configuration_checksum = (
        save_configuration_artifact(configuration)
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        provider_lock
    )

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
            assert "-destroy" in command
            (Path(cwd) / "tfplan").write_bytes(
                b"mock-destroy-plan"
            )

        if command[1] == "show":
            return completed_process(
                stdout=json.dumps({"resource_changes": []})
            )

        return completed_process()

    parsed_plan = {
        "resources": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "actions": ["delete"],
            }
        ],
        "resource_summary": {"delete": 1},
        "review_summary": {},
        "dependency_analysis": {},
        "terraform_version": "1.15.6",
        "format_version": "1.2",
    }

    monkeypatch.setattr(
        execution_module.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        execution_module,
        "parse_terraform_plan",
        lambda raw_plan: parsed_plan,
    )
    monkeypatch.setattr(
        execution_module,
        "evaluate_terraform_security",
        lambda raw_plan: {"passed": True, "findings": []},
    )

    result = execute_terraform_destroy_plan(
        configuration_artifact_path=configuration_path,
        configuration_artifact_sha256=configuration_checksum,
        provider_lock_path=lock_path,
        provider_lock_sha256=lock_checksum,
    )

    assert [command[1] for command in commands] == [
        "init",
        "plan",
        "show",
    ]

    assert result["status"] == "succeeded"
    assert result["operation"] == "deprovision"
    assert result["deployment_enabled"] is False
    assert result["source_verified"] is False

    saved_plan = load_plan_artifact(
        result["artifact_path"],
        result["artifact_sha256"],
    )

    assert saved_plan == b"mock-destroy-plan"
    assert len(result["plan_fingerprint"]) == 64

    assert not any(
        command[1] in ("apply", "destroy")
        for command in commands
    )


@pytest.mark.parametrize(
    "unexpected_actions",
    [
        ["create"],
        ["update"],
        ["delete", "create"],
        ["create", "delete"],
    ],
)
def test_destroy_plan_rejects_unexpected_managed_actions(
    monkeypatch,
    unexpected_actions,
):
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform { required_version = ">= 1.10.0" }'
        )
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        b"# Test provider lock file\n"
    )

    def fake_run(command, *, cwd, **kwargs):
        if command[1] == "plan":
            (Path(cwd) / "tfplan").write_bytes(b"mock-plan")

        if command[1] == "show":
            return completed_process(
                stdout=json.dumps({"resource_changes": []})
            )

        return completed_process()

    parsed_plan = {
        "resources": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "actions": unexpected_actions,
            }
        ],
        "resource_summary": {},
        "review_summary": {},
        "dependency_analysis": {},
        "terraform_version": "1.15.6",
        "format_version": "1.2",
    }

    monkeypatch.setattr(
        execution_module.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        execution_module,
        "parse_terraform_plan",
        lambda raw_plan: parsed_plan,
    )
    monkeypatch.setattr(
        execution_module,
        "evaluate_terraform_security",
        lambda raw_plan: {"passed": True, "findings": []},
    )

    with pytest.raises(TerraformExecutionError) as exc:
        execute_terraform_destroy_plan(
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256=configuration_checksum,
            provider_lock_path=lock_path,
            provider_lock_sha256=lock_checksum,
        )

    assert exc.value.code == "DESTROY_PLAN_UNEXPECTED_ACTION"


def test_destroy_plan_rejects_empty_managed_deletions(
    monkeypatch,
):
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform { required_version = ">= 1.10.0" }'
        )
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        b"# Test provider lock file\n"
    )

    def fake_run(command, *, cwd, **kwargs):
        if command[1] == "plan":
            (Path(cwd) / "tfplan").write_bytes(b"mock-plan")

        if command[1] == "show":
            return completed_process(
                stdout=json.dumps({"resource_changes": []})
            )

        return completed_process()

    parsed_plan = {
        "resources": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "actions": ["no-op"],
            }
        ],
        "resource_summary": {},
        "review_summary": {},
        "dependency_analysis": {},
        "terraform_version": "1.15.6",
        "format_version": "1.2",
    }

    monkeypatch.setattr(
        execution_module.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        execution_module,
        "parse_terraform_plan",
        lambda raw_plan: parsed_plan,
    )
    monkeypatch.setattr(
        execution_module,
        "evaluate_terraform_security",
        lambda raw_plan: {"passed": True, "findings": []},
    )

    with pytest.raises(TerraformExecutionError) as exc:
        execute_terraform_destroy_plan(
            configuration_artifact_path=configuration_path,
            configuration_artifact_sha256=configuration_checksum,
            provider_lock_path=lock_path,
            provider_lock_sha256=lock_checksum,
        )

    assert exc.value.code == "DESTROY_PLAN_NO_RESOURCES"


def test_destroy_plan_allows_data_source_reads(
    monkeypatch,
):
    configuration_path, configuration_checksum = (
        save_configuration_artifact(
            'terraform { required_version = ">= 1.10.0" }'
        )
    )
    lock_path, lock_checksum = save_provider_lock_artifact(
        b"# Test provider lock file\n"
    )

    captured_security_plan = {}

    def fake_run(command, *, cwd, **kwargs):
        if command[1] == "plan":
            (Path(cwd) / "tfplan").write_bytes(b"mock-plan")

        if command[1] == "show":
            return completed_process(
                stdout=json.dumps(
                    {
                        "resource_changes": [
                            {
                                "address": "aws_instance.this",
                                "mode": "managed",
                                "type": "aws_instance",
                                "name": "this",
                                "change": {
                                    "actions": ["delete"],
                                },
                            },
                            {
                                "address": "data.aws_ami.example",
                                "mode": "data",
                                "type": "aws_ami",
                                "name": "example",
                                "change": {
                                    "actions": ["read"],
                                },
                            },
                        ]
                    }
                )
            )

        return completed_process()

    parsed_plan = {
        "resources": [
            {
                "address": "aws_instance.this",
                "mode": "managed",
                "type": "aws_instance",
                "name": "this",
                "actions": ["delete"],
            },
            {
                "address": "data.aws_ami.example",
                "mode": "data",
                "type": "aws_ami",
                "name": "example",
                "actions": ["read"],
            },
        ],
        "resource_summary": {"delete": 1},
        "review_summary": {},
        "dependency_analysis": {},
        "terraform_version": "1.15.6",
        "format_version": "1.2",
    }

    monkeypatch.setattr(
        execution_module.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        execution_module,
        "parse_terraform_plan",
        lambda raw_plan: parsed_plan,
    )

    def fake_security_evaluation(raw_plan):
        captured_security_plan.update(raw_plan)
        return {"passed": True, "findings": []}

    monkeypatch.setattr(
        execution_module,
        "evaluate_terraform_security",
        fake_security_evaluation,
    )

    result = execute_terraform_destroy_plan(
        configuration_artifact_path=configuration_path,
        configuration_artifact_sha256=configuration_checksum,
        provider_lock_path=lock_path,
        provider_lock_sha256=lock_checksum,
    )

    assert result["status"] == "succeeded"
    assert len(captured_security_plan["resource_changes"]) == 1
    assert (
        captured_security_plan["resource_changes"][0]["mode"]
        == "managed"
    )

