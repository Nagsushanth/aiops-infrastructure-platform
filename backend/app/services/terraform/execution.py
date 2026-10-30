
"""Run a real Terraform plan without applying infrastructure changes."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import (
    TerraformGenerationError,
    generate_terraform,
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
    ).encode("utf-8")


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

    matched = [marker for marker in markers if marker in diagnostic]
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
        raise TerraformExecutionError(f"{stage}_TIMEOUT") from exc
    except OSError as exc:
        raise TerraformExecutionError(f"{stage}_START_FAILED") from exc

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

        raise TerraformExecutionError(f"{stage}_FAILED_{category}")

    return result


def execute_terraform_plan(
    specification: ValidatedInfrastructureSpecification,
    *,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Generate a real plan and return fingerprints plus a sanitized summary.

    This function never runs terraform apply. AWS credentials and suitable
    read permissions may be needed because the configuration uses AWS data
    sources. Call only from a trusted, authenticated server-side workflow.
    """
    if not 1 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be between 1 and 600.")

    try:
        configuration = generate_terraform(specification)
    except TerraformGenerationError as exc:
        raise TerraformExecutionError(
            "UNSUPPORTED_SPECIFICATION"
        ) from exc

    specification_fingerprint = _sha256(
        _canonical_specification(specification)
    )

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

    # Explicitly choose the temporary filesystem. If TMPDIR is set by the
    # operator, respect it; otherwise use the system default.
    temp_root = Path(
        env.get("TMPDIR") or tempfile.gettempdir()
    ).expanduser()
    temp_root.mkdir(parents=True, exist_ok=True)

    env["TMPDIR"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env["TMP"] = str(temp_root)
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"

    with tempfile.TemporaryDirectory(
        prefix="aiops-terraform-",
        dir=temp_root,
    ) as temp:
        workspace = Path(temp)

        (workspace / "main.tf").write_text(
            configuration,
            encoding="utf-8",
        )

        _run(
            [
                "terraform",
                "init",
                "-backend=false",
                "-input=false",
                "-no-color",
            ],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        _run(
            [
                "terraform",
                "plan",
                "-input=false",
                "-no-color",
                "-refresh=false",
                "-out=tfplan",
            ],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=env,
        )

        plan_file = workspace / "tfplan"
        if not plan_file.is_file():
            raise TerraformExecutionError("PLAN_ARTIFACT_MISSING")

        plan_fingerprint = _sha256(plan_file.read_bytes())

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
            raise TerraformExecutionError("PLAN_PARSE_FAILED") from exc

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

        return {
            "status": "succeeded",
            "preview_type": "trusted_server_terraform_plan",
            "is_real_terraform_plan": True,
            "source_verified": False,
            "deployment_enabled": False,
            "specification_fingerprint": specification_fingerprint,
            "plan_fingerprint": plan_fingerprint,
            "plan_summary": summary,
        }
