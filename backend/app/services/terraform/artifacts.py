
"""Secure local storage for binary Terraform plan artifacts."""

import hashlib
import os
import secrets
import tempfile
from pathlib import Path


class TerraformArtifactError(RuntimeError):
    """Safe error raised during Terraform artifact operations."""


def _artifact_directory() -> Path:
    """Resolve the private artifact directory."""
    project_root = Path(__file__).resolve().parents[4]

    configured_directory = os.getenv("AIOPS_TERRAFORM_ARTIFACT_DIR")

    directory = (
        Path(configured_directory).expanduser()
        if configured_directory
        else project_root / ".terraform-artifacts"
    )

    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve(strict=True)

    if not directory.is_dir():
        raise TerraformArtifactError("ARTIFACT_DIRECTORY_INVALID")

    try:
        directory.chmod(0o700)
    except OSError as exc:
        raise TerraformArtifactError(
            "ARTIFACT_DIRECTORY_PERMISSION_FAILED"
        ) from exc

    return directory


def _resolve_artifact_path(artifact_path: str) -> Path:
    """Reject paths outside the private artifact directory."""
    if not artifact_path:
        raise TerraformArtifactError("ARTIFACT_PATH_INVALID")

    directory = _artifact_directory()
    candidate = Path(artifact_path)

    if candidate.is_symlink():
        raise TerraformArtifactError("ARTIFACT_PATH_INVALID")

    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TerraformArtifactError("ARTIFACT_NOT_FOUND") from exc

    if not resolved.is_relative_to(directory):
        raise TerraformArtifactError("ARTIFACT_PATH_INVALID")

    if not resolved.is_file():
        raise TerraformArtifactError("ARTIFACT_NOT_FOUND")

    return resolved


def save_plan_artifact(plan_bytes: bytes) -> tuple[str, str]:
    """Save a binary Terraform plan and return its path and SHA-256."""
    if not isinstance(plan_bytes, bytes) or not plan_bytes:
        raise TerraformArtifactError("ARTIFACT_CONTENT_INVALID")

    directory = _artifact_directory()

    filename = f"{secrets.token_hex(24)}.tfplan"
    artifact_path = directory / filename

    file_descriptor = None

    try:
        file_descriptor = os.open(
            artifact_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(file_descriptor, "wb") as artifact_file:
            file_descriptor = None
            artifact_file.write(plan_bytes)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())

    except OSError as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)

        try:
            artifact_path.unlink(missing_ok=True)
        except OSError:
            pass

        raise TerraformArtifactError(
            "ARTIFACT_SAVE_FAILED"
        ) from exc

    checksum = hashlib.sha256(plan_bytes).hexdigest()

    return str(artifact_path), checksum


def load_plan_artifact(
    artifact_path: str,
    expected_sha256: str,
) -> bytes:
    """Load an artifact only after verifying its SHA-256 checksum."""
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in expected_sha256
        )
    ):
        raise TerraformArtifactError("ARTIFACT_CHECKSUM_INVALID")

    path = _resolve_artifact_path(artifact_path)

    try:
        plan_bytes = path.read_bytes()
    except OSError as exc:
        raise TerraformArtifactError(
            "ARTIFACT_READ_FAILED"
        ) from exc

    actual_sha256 = hashlib.sha256(plan_bytes).hexdigest()

    if not secrets.compare_digest(
        actual_sha256,
        expected_sha256.lower(),
    ):
        raise TerraformArtifactError("ARTIFACT_CHECKSUM_MISMATCH")

    return plan_bytes


def delete_plan_artifact(artifact_path: str) -> bool:
    """Delete an artifact only if it belongs to the private directory."""
    if not artifact_path:
        return False

    path = _resolve_artifact_path(artifact_path)

    try:
        path.unlink()
    except OSError as exc:
        raise TerraformArtifactError(
            "ARTIFACT_DELETE_FAILED"
        ) from exc

    return True


def save_configuration_artifact(
    configuration: str,
) -> tuple[str, str]:
    """Save Terraform configuration securely and return path and SHA-256."""

    if not isinstance(configuration, str) or not configuration.strip():
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_CONTENT_INVALID"
        )

    configuration_bytes = configuration.encode("utf-8")
    directory = _artifact_directory()

    artifact_path = directory / f"{secrets.token_hex(24)}.tf"
    file_descriptor = None

    try:
        file_descriptor = os.open(
            artifact_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(file_descriptor, "wb") as artifact_file:
            file_descriptor = None
            artifact_file.write(configuration_bytes)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())

    except OSError as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)

        try:
            artifact_path.unlink(missing_ok=True)
        except OSError:
            pass

        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_SAVE_FAILED"
        ) from exc

    checksum = hashlib.sha256(configuration_bytes).hexdigest()

    return str(artifact_path), checksum


def load_configuration_artifact(
    artifact_path: str,
    expected_sha256: str,
) -> str:
    """Load Terraform configuration after verifying its SHA-256."""

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in expected_sha256
        )
    ):
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_CHECKSUM_INVALID"
        )

    path = _resolve_artifact_path(artifact_path)

    if path.suffix != ".tf":
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_PATH_INVALID"
        )

    try:
        configuration_bytes = path.read_bytes()
    except OSError as exc:
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_READ_FAILED"
        ) from exc

    actual_sha256 = hashlib.sha256(
        configuration_bytes
    ).hexdigest()

    if not secrets.compare_digest(
        actual_sha256,
        expected_sha256.lower(),
    ):
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_CHECKSUM_MISMATCH"
        )

    try:
        return configuration_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_ENCODING_INVALID"
        ) from exc


def delete_configuration_artifact(
    artifact_path: str,
) -> bool:
    """Delete a configuration artifact from the private directory."""

    if not artifact_path:
        return False

    path = _resolve_artifact_path(artifact_path)

    if path.suffix != ".tf":
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_PATH_INVALID"
        )

    try:
        path.unlink()
    except OSError as exc:
        raise TerraformArtifactError(
            "CONFIGURATION_ARTIFACT_DELETE_FAILED"
        ) from exc

    return True


def delete_provider_lock_artifact(artifact_path: str) -> bool:
    """Delete a provider lock artifact only if it belongs to the private directory."""
    if not artifact_path:
        return False

    path = _resolve_artifact_path(artifact_path)

    if path.suffixes[-2:] != [".lock", ".hcl"]:
        raise TerraformArtifactError("LOCK_ARTIFACT_PATH_INVALID")

    try:
        path.unlink()
    except OSError as exc:
        raise TerraformArtifactError(
            "LOCK_ARTIFACT_DELETE_FAILED"
        ) from exc

    return True



def save_provider_lock_artifact(lock_bytes: bytes) -> tuple[str, str]:
    """Save a Terraform provider lock file securely and return path and SHA-256."""

    if not isinstance(lock_bytes, bytes) or not lock_bytes:
        raise TerraformArtifactError("LOCK_ARTIFACT_CONTENT_INVALID")

    directory = _artifact_directory()
    artifact_path = directory / f"{secrets.token_hex(24)}.lock.hcl"

    file_descriptor = None

    try:
        file_descriptor = os.open(
            artifact_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        with os.fdopen(file_descriptor, "wb") as artifact_file:
            file_descriptor = None
            artifact_file.write(lock_bytes)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())

    except OSError as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)

        try:
            artifact_path.unlink(missing_ok=True)
        except OSError:
            pass

        raise TerraformArtifactError(
            "LOCK_ARTIFACT_SAVE_FAILED"
        ) from exc

    checksum = hashlib.sha256(lock_bytes).hexdigest()

    return str(artifact_path), checksum


def load_provider_lock_artifact(
    artifact_path: str,
    expected_sha256: str,
) -> bytes:
    """Load a provider lock file only after verifying its SHA-256."""

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in expected_sha256
        )
    ):
        raise TerraformArtifactError("LOCK_ARTIFACT_CHECKSUM_INVALID")

    path = _resolve_artifact_path(artifact_path)

    if path.suffixes[-2:] != [".lock", ".hcl"]:
        raise TerraformArtifactError("LOCK_ARTIFACT_PATH_INVALID")

    try:
        lock_bytes = path.read_bytes()
    except OSError as exc:
        raise TerraformArtifactError(
            "LOCK_ARTIFACT_READ_FAILED"
        ) from exc

    actual_sha256 = hashlib.sha256(lock_bytes).hexdigest()

    if not secrets.compare_digest(
        actual_sha256,
        expected_sha256.lower(),
    ):
        raise TerraformArtifactError(
            "LOCK_ARTIFACT_CHECKSUM_MISMATCH"
        )

    return lock_bytes
