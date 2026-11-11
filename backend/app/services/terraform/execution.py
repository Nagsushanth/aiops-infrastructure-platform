"""Run a real Terraform plan without applying infrastructure changes."""

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    delete_configuration_artifact,
    load_configuration_artifact,
    load_plan_artifact,
    load_provider_lock_artifact,
    save_configuration_artifact,
    save_plan_artifact,
    save_provider_lock_artifact,
)
from backend.app.services.terraform.backend_config import (
    build_s3_backend_config,
)
from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.plan_parser import (
    TerraformPlanParsingError,
    parse_terraform_plan,
)
from backend.app.services.terraform.security_policy import (
    evaluate_terraform_security,
)
from backend.app.core.terraform_pricing import (
    BUDGET_THRESHOLD_USD,
    PRICING_CATALOG,
)
from backend.app.services.terraform.generator import (
    INSTANCE_CATALOG,
    TerraformGenerationError,
    generate_terraform,
)
from backend.app.services.terraform.cost_estimator import (
    estimate_ec2_monthly_cost,
)
from contextlib import contextmanager
from collections.abc import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env")


class TerraformExecutionError(RuntimeError):
    """A safe, non-sensitive Terraform execution failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Terraform planning failed ({code}).")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_specification(
    specification: ValidatedInfrastructureSpecification,
) -> bytes:
    payload = specification.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _inject_s3_backend(
    configuration: str,
    backend_config: dict[str, str],
) -> str:
    """Inject and verify the request-specific S3 backend configuration."""

    terraform_block = "terraform {\n"

    if configuration.count(terraform_block) != 1:
        raise TerraformExecutionError(
            "BACKEND_BLOCK_INJECTION_FAILED"
        )

    if 'backend "' in configuration:
        raise TerraformExecutionError(
            "BACKEND_BLOCK_ALREADY_CONFIGURED"
        )

    backend_block = (
        '  backend "s3" {\n'
        f'    bucket       = {json.dumps(backend_config["bucket"])}\n'
        f'    key          = {json.dumps(backend_config["key"])}\n'
        f'    region       = {json.dumps(backend_config["region"])}\n'
        '    encrypt      = true\n'
        '    use_lockfile = true\n'
        '  }\n'
    )

    configured = configuration.replace(
        terraform_block,
        terraform_block + backend_block,
        1,
    )

    if configured.count('backend "s3"') != 1:
        raise TerraformExecutionError(
            "BACKEND_BLOCK_INJECTION_FAILED"
        )

    return configured


def _failure_category(diagnostic: str) -> str:
    """Classify Terraform output without exposing its raw contents."""
    diagnostic = diagnostic.lower()

    if any(
        marker in diagnostic
        for marker in (
            "accessdenied",
            "unauthorized",
            "not authorized",
            "permission denied",
            "unable to locate credentials",
            "no valid credential sources found",
            "failed to retrieve credentials",
            "invalidclienttokenid",
            "invalid security token",
            "expiredtoken",
            "authfailure",
            "signaturedoesnotmatch",
        )
    ):
        return "PERMISSION"

    if any(
        marker in diagnostic
        for marker in (
            "could not connect",
            "connection timed out",
            "network is unreachable",
            "failed to establish a new connection",
        )
    ):
        return "NETWORK"

    if (
        "failed to query available provider packages" in diagnostic
        or "could not retrieve the list of available versions" in diagnostic
    ):
        return "PROVIDER_DOWNLOAD"

    if "no matching ami" in diagnostic or "no results" in diagnostic:
        return "DATA_SOURCE"

    if any(
        marker in diagnostic
        for marker in (
            "unsupported argument",
            "missing required argument",
            "unsupported block type",
            "invalid reference",
            "invalid expression",
            "invalid value for",
            "invalid configuration",
            "error: invalid ",
            "configuration for provider",
        )
    ):
        return "CONFIGURATION"

    return "GENERAL"


def _safe_diagnostic_marker(diagnostic: str) -> str:
    """Return a known marker only; never return raw Terraform output."""
    diagnostic = diagnostic.lower()

    markers = (
        "accessdenied",
        "unauthorized",
        "not authorized",
        "permission denied",
        "unable to locate credentials",
        "no valid credential sources found",
        "failed to retrieve credentials",
        "invalidclienttokenid",
        "invalid security token",
        "expiredtoken",
        "authfailure",
        "signaturedoesnotmatch",
        "no matching ami",
        "no results",
        "could not connect",
        "connection timed out",
        "network is unreachable",
        "failed to establish a new connection",
        "failed to query available provider packages",
        "could not retrieve the list of available versions",
        "unsupported argument",
        "missing required argument",
        "unsupported block type",
        "invalid reference",
        "invalid expression",
        "invalid value for",
        "invalid configuration",
        "configuration for provider",
    )

    matched = [
        marker for marker in markers
        if marker in diagnostic
    ]

    return ", ".join(matched) if matched else "none matched"


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run a fixed Terraform command and classify failures safely."""
    stage = command[1].upper() if len(command) > 1 else "UNKNOWN"

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TerraformExecutionError(
            f"{stage}_TIMEOUT"
        ) from exc
    except OSError as exc:
        raise TerraformExecutionError(
            f"{stage}_START_FAILED"
        ) from exc

    if result.returncode != 0:
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        diagnostic = f"{stdout}\n{stderr}"

        category = _failure_category(diagnostic)
        marker = _safe_diagnostic_marker(diagnostic)

        # Log only fixed, recognized marker phrases, never raw output.
        print(
            f"Terraform {stage} failed: "
            f"category={category}; diagnostic_marker={marker}"
        )

        raise TerraformExecutionError(
            f"{stage}_FAILED_{category}"
        )

    return result

def _terminate_process_group(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 5.0,
) -> None:
    """Terminate the process group, including surviving descendants."""

    if os.name == "posix":
        try:
            # The process group can outlive its original parent.
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            pass
    else:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass

    if os.name == "posix":
        try:
            # Escalate to SIGKILL if any process remains in the group.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    elif process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass

def _run_controlled_process(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
    lease_renewal_callback,
    lease_renewal_interval_seconds: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess while monitoring timeout and worker ownership."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise ValueError("timeout_seconds must be a positive integer.")

    if (
        isinstance(lease_renewal_interval_seconds, bool)
        or not isinstance(lease_renewal_interval_seconds, (int, float))
        or lease_renewal_interval_seconds <= 0
    ):
        raise ValueError("Lease renewal interval must be positive.")

    if not callable(lease_renewal_callback):
        raise ValueError("A lease renewal callback is required.")

    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise TerraformExecutionError(
            "PROCESS_START_FAILED"
        ) from exc

    deadline = time.monotonic() + timeout_seconds
    next_renewal = (
        time.monotonic() + lease_renewal_interval_seconds
    )

    try:
        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                _terminate_process_group(process)
                process.communicate()
                raise TerraformExecutionError("PROCESS_TIMEOUT")

            wait_seconds = min(
                remaining,
                max(0.01, next_renewal - time.monotonic()),
            )

            try:
                stdout, stderr = process.communicate(
                    timeout=wait_seconds
                )
                break
            except subprocess.TimeoutExpired:
                pass

            now = time.monotonic()

            if now >= deadline:
                _terminate_process_group(process)
                process.communicate()
                raise TerraformExecutionError("PROCESS_TIMEOUT")

            if now >= next_renewal:
                try:
                    ownership_valid = lease_renewal_callback()
                except Exception as exc:
                    _terminate_process_group(process)
                    process.communicate()
                    raise TerraformExecutionError(
                        "WORKER_LEASE_RENEWAL_FAILED"
                    ) from exc

                if ownership_valid is not True:
                    _terminate_process_group(process)
                    process.communicate()
                    raise TerraformExecutionError(
                        "WORKER_LEASE_LOST"
                    )

                next_renewal = (
                    time.monotonic()
                    + lease_renewal_interval_seconds
                )

    except BaseException:
        if process.poll() is None:
            _terminate_process_group(process)
            process.communicate()
        raise

    result = subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )

    if result.returncode != 0:
        diagnostic = f"{result.stdout}\n{result.stderr}"
        category = _failure_category(diagnostic)
        marker = _safe_diagnostic_marker(diagnostic)

        print(
            "Controlled process failed: "
            f"category={category}; diagnostic_marker={marker}"
        )

        raise TerraformExecutionError(
            f"PROCESS_FAILED_{category}"
        )

    return result


@contextmanager
def reconstructed_terraform_workspace(
    *,
    configuration_artifact_path: str,
    configuration_artifact_sha256: str,
    provider_lock_path: str,
    provider_lock_sha256: str,
    plan_artifact_path: str,
    plan_artifact_sha256: str,
    temp_root: Path | None = None,
) -> Iterator[Path]:
    """Reconstruct an isolated workspace from verified Terraform artifacts."""

    try:
        configuration = load_configuration_artifact(
            configuration_artifact_path,
            configuration_artifact_sha256,
        )
        provider_lock = load_provider_lock_artifact(
            provider_lock_path,
            provider_lock_sha256,
        )
        plan_bytes = load_plan_artifact(
            plan_artifact_path,
            plan_artifact_sha256,
        )
    except TerraformArtifactError as exc:
        raise TerraformExecutionError(
            "WORKSPACE_ARTIFACT_VALIDATION_FAILED"
        ) from exc

    if not configuration.strip() or not provider_lock or not plan_bytes:
        raise TerraformExecutionError(
            "WORKSPACE_ARTIFACT_CONTENT_INVALID"
        )

    with tempfile.TemporaryDirectory(
        prefix="aiops-terraform-apply-",
        dir=temp_root,
    ) as temp:
        workspace = Path(temp)

        files = {
            "main.tf": configuration.encode("utf-8"),
            ".terraform.lock.hcl": provider_lock,
            "tfplan": plan_bytes,
        }

        try:
            for filename, content in files.items():
                file_path = workspace / filename
                file_descriptor = os.open(
                    file_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )

                with os.fdopen(file_descriptor, "wb") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())

        except OSError as exc:
            raise TerraformExecutionError(
                "WORKSPACE_RECONSTRUCTION_FAILED"
            ) from exc

        # Exceptions from the caller must propagate unchanged.
        yield workspace


def initialize_terraform_apply_workspace(
    workspace: Path,
    *,
    timeout_seconds: int,
    env: dict[str, str],
    lease_renewal_callback,
) -> subprocess.CompletedProcess[str]:
    """Initialize a reconstructed workspace without changing provider locks."""

    return _run_controlled_process(
        [
            "terraform",
            "init",
            "-input=false",
            "-no-color",
            "-lockfile=readonly",
        ],
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        env=env,
        lease_renewal_callback=lease_renewal_callback,
    )


def build_terraform_environment() -> tuple[dict[str, str], Path]:
    """Build a restricted Terraform environment and private temp root."""

    allowed_env_vars = (
        "PATH",
        "HOME",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_EC2_METADATA_DISABLED",
    )

    env = {
        key: os.environ[key]
        for key in allowed_env_vars
        if key in os.environ
    }

    env.setdefault(
        "AWS_SHARED_CREDENTIALS_FILE",
        os.path.expanduser("~/.aws/credentials"),
    )
    env.setdefault(
        "AWS_CONFIG_FILE",
        os.path.expanduser("~/.aws/config"),
    )

    temp_root = Path(
        env.get("TMPDIR") or tempfile.gettempdir()
    ).expanduser()

    temp_root.mkdir(parents=True, exist_ok=True)

    env["TMPDIR"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"

    return env, temp_root


def execute_terraform_plan(
    specification: ValidatedInfrastructureSpecification,
    *,
    infrastructure_request_id: int,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Generate a real plan and return fingerprints plus a sanitized summary.

    This function never runs terraform apply. AWS credentials and suitable
    read permissions may be needed because the configuration uses AWS data
    sources. Call only from a trusted, authenticated server-side workflow.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 600
    ):
        raise ValueError(
            "timeout_seconds must be an integer between 1 and 600."
        )

    try:
        configuration = generate_terraform(specification)
    except TerraformGenerationError as exc:
        raise TerraformExecutionError(
            "UNSUPPORTED_SPECIFICATION"
        ) from exc

    specification_fingerprint = _sha256(
        _canonical_specification(specification)
    )

    bucket = os.getenv("TF_STATE_BUCKET", "").strip()
    if not bucket:
        raise TerraformExecutionError(
            "STATE_BACKEND_NOT_CONFIGURED"
        )

    try:
        backend_config = build_s3_backend_config(
            infrastructure_request_id=infrastructure_request_id,
            bucket=bucket,
            region=os.getenv("TF_STATE_REGION", "ap-south-1"),
            prefix=os.getenv("TF_STATE_PREFIX", "training/requests"),
        )
    except ValueError as exc:
        raise TerraformExecutionError(
            "INVALID_STATE_BACKEND_CONFIG"
        ) from exc

    env, temp_root = build_terraform_environment()

    with tempfile.TemporaryDirectory(
        prefix="aiops-terraform-",
        dir=temp_root,
    ) as temp:
        workspace = Path(temp)

        configuration = _inject_s3_backend(
            configuration,
            backend_config,
        )

        (workspace / "main.tf").write_text(
            configuration,
            encoding="utf-8",
        )

        _run(
            [
                "terraform",
                "init",
                "-input=false",
                "-no-color",
            ],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        # Preserve the exact provider selections made during terraform init.
        lock_file = workspace / ".terraform.lock.hcl"

        if not lock_file.is_file():
            raise TerraformExecutionError(
                "PROVIDER_LOCK_FILE_MISSING"
            )

        try:
            provider_lock_path, provider_lock_sha256 = (
                save_provider_lock_artifact(
                    lock_file.read_bytes()
                )
            )
        except (OSError, TerraformArtifactError) as exc:
            raise TerraformExecutionError(
                "PROVIDER_LOCK_FILE_SAVE_FAILED"
            ) from exc

        _run(
            [
                "terraform",
                "plan",
                "-input=false",
                "-no-color",
                "-out=tfplan",
            ],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        plan_file = workspace / "tfplan"
        if not plan_file.is_file():
            raise TerraformExecutionError(
                "PLAN_ARTIFACT_MISSING"
            )

        plan_bytes = plan_file.read_bytes()
        plan_fingerprint = _sha256(plan_bytes)

        shown = _run(
            ["terraform", "show", "-json", "tfplan"],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        try:
            raw_plan = json.loads(shown.stdout)
            parsed = parse_terraform_plan(raw_plan)
            security_analysis = evaluate_terraform_security(raw_plan)
        except (
            json.JSONDecodeError,
            TerraformPlanParsingError,
            ValueError,
        ) as exc:
            raise TerraformExecutionError(
                "PLAN_PARSE_FAILED"
            ) from exc

        resources = [
            {
                "address": item.get("address"),
                "mode": item.get("mode"),
                "type": item.get("type"),
                "name": item.get("name"),
                "actions": item.get("actions", []),
            }
            for item in parsed["resources"]
        ]

        instance_type = INSTANCE_CATALOG[
            (specification.vcpus, specification.memory_gb)
        ]

        cost_estimate = estimate_ec2_monthly_cost(
            instance_type=instance_type,
            region=specification.region,
            pricing_catalog=PRICING_CATALOG,
            budget_threshold=BUDGET_THRESHOLD_USD,
        )

        summary = {
            "resource_summary": parsed["resource_summary"],
            "resources": resources,
            "terraform_version": parsed.get("terraform_version"),
            "format_version": parsed.get("format_version"),
            "security_analysis": security_analysis,
            "cost_estimate": cost_estimate,
        }

        # Persist the exact Terraform configuration used to generate this plan.
        # This includes the injected S3 backend configuration.
        try:
            configuration_artifact_path, configuration_artifact_sha256 = (
                save_configuration_artifact(configuration)
            )
        except (OSError, TerraformArtifactError) as exc:
            raise TerraformExecutionError(
                "CONFIGURATION_ARTIFACT_SAVE_FAILED"
            ) from exc

        # Persist the exact binary plan before the temporary workspace
        # is removed. The artifact remains private on the server.
        try:
            artifact_path, artifact_sha256 = save_plan_artifact(
                plan_bytes
            )
        except (OSError, TerraformArtifactError) as exc:
            delete_configuration_artifact(
                configuration_artifact_path
            )
            raise TerraformExecutionError(
                "PLAN_ARTIFACT_SAVE_FAILED"
            ) from exc
            raise

        return {
            "status": "succeeded",
            "preview_type": "trusted_server_terraform_plan",
            "is_real_terraform_plan": True,
            "source_verified": False,
            "deployment_enabled": False,
            "specification_fingerprint": specification_fingerprint,
            "plan_fingerprint": plan_fingerprint,
            "plan_summary": summary,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha256,
            "provider_lock_path": provider_lock_path,
            "provider_lock_sha256": provider_lock_sha256,
            "configuration_artifact_path": configuration_artifact_path,
            "configuration_artifact_sha256": configuration_artifact_sha256,
        }
