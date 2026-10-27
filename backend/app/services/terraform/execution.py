
"""Run a real Terraform plan without applying infrastructure changes."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.app.services.terraform.security_policy import (
    evaluate_terraform_security,
)

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.generator import (
    generate_terraform,
    TerraformGenerationError,
)
from backend.app.services.terraform.plan_parser import (
    parse_terraform_plan,
    TerraformPlanParsingError,
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


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Execute a fixed Terraform command without a shell."""
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
        raise TerraformExecutionError("COMMAND_TIMEOUT") from exc
    except OSError as exc:
        raise TerraformExecutionError("COMMAND_START_FAILED") from exc

    if result.returncode != 0:
        # Do not expose or log raw Terraform output; it may contain sensitive data.
        raise TerraformExecutionError("COMMAND_FAILED")

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
        raise TerraformExecutionError("UNSUPPORTED_SPECIFICATION") from exc

    specification_fingerprint = _sha256(
        _canonical_specification(specification)
    )

    
# Pass only the environment variables Terraform needs.
# Do not forward unrelated application secrets.
    allowed_env_vars = (
    "PATH",
    "HOME",
    "TMPDIR",
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
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"


    with tempfile.TemporaryDirectory(prefix="aiops-terraform-") as temp:
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

        # Fingerprint the exact binary plan produced by Terraform.
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

        # Never return raw before/after attributes: parser redaction alone
        # cannot guarantee that every sensitive value is marked correctly.
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

        summary = {
            "resource_summary": parsed["resource_summary"],
            "resources": resources,
            "terraform_version": parsed.get("terraform_version"),
            "format_version": parsed.get("format_version"),
            "security_analysis": security_analysis,
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
