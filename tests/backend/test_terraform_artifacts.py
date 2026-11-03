
import hashlib
import stat

import pytest

from backend.app.services.terraform.artifacts import (
    TerraformArtifactError,
    save_plan_artifact,
    load_plan_artifact,
    delete_plan_artifact,
)


def test_save_and_load_artifact():
    content = b"mock-terraform-binary-plan"

    path, checksum = save_plan_artifact(content)

    assert checksum == hashlib.sha256(content).hexdigest()
    assert load_plan_artifact(path, checksum) == content

    delete_plan_artifact(path)


def test_artifact_has_restricted_permissions():
    content = b"private-plan"

    path, _ = save_plan_artifact(content)

    permissions = stat.S_IMODE(
        __import__("pathlib").Path(path).stat().st_mode
    )

    assert permissions == 0o600

    delete_plan_artifact(path)


def test_checksum_mismatch_is_rejected():
    path, _ = save_plan_artifact(b"original-plan")

    with pytest.raises(TerraformArtifactError) as exc:
        load_plan_artifact(path, "0" * 64)

    assert exc.value.args[0] == "ARTIFACT_CHECKSUM_MISMATCH"

    delete_plan_artifact(path)


def test_invalid_checksum_is_rejected():
    path, _ = save_plan_artifact(b"original-plan")

    with pytest.raises(TerraformArtifactError) as exc:
        load_plan_artifact(path, "invalid")

    assert exc.value.args[0] == "ARTIFACT_CHECKSUM_INVALID"

    delete_plan_artifact(path)


def test_empty_artifact_is_rejected():
    with pytest.raises(TerraformArtifactError) as exc:
        save_plan_artifact(b"")

    assert exc.value.args[0] == "ARTIFACT_CONTENT_INVALID"


def test_artifact_outside_storage_directory_is_rejected(tmp_path):
    outside_file = tmp_path / "outside.tfplan"
    outside_file.write_bytes(b"private-plan")

    checksum = hashlib.sha256(b"private-plan").hexdigest()

    with pytest.raises(TerraformArtifactError) as exc:
        load_plan_artifact(str(outside_file), checksum)

    assert exc.value.args[0] == "ARTIFACT_PATH_INVALID"


def test_artifact_deletion():
    path, _ = save_plan_artifact(b"delete-me")

    assert delete_plan_artifact(path) is True

    with pytest.raises(TerraformArtifactError):
        load_plan_artifact(path, hashlib.sha256(b"delete-me").hexdigest())
