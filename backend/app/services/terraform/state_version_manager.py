"""Manage Terraform state version history and integrity."""

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.models.terraform_state_version import TerraformStateVersion


class TerraformStateVersionError(RuntimeError):
    """Safe error raised during Terraform state version operations."""


def record_state_version(
    db: Session,
    infrastructure_request_id: int,
    *,
    version_id: str,
    state_serial: int,
    state_lineage: str,
    state_sha256: str,
    size_bytes: int,
) -> TerraformStateVersion:
    """Record metadata for a previously persisted Terraform state version."""

    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise TerraformStateVersionError("STATE_REQUEST_ID_INVALID")

    if not isinstance(version_id, str) or not version_id.strip():
        raise TerraformStateVersionError("STATE_VERSION_ID_INVALID")

    if (
        not isinstance(state_serial, int)
        or isinstance(state_serial, bool)
        or state_serial < 0
    ):
        raise TerraformStateVersionError("STATE_SERIAL_INVALID")

    try:
        parsed_lineage = uuid.UUID(state_lineage)
        if str(parsed_lineage) != state_lineage.lower():
            raise ValueError("Non-canonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise TerraformStateVersionError("STATE_LINEAGE_INVALID")

    if not isinstance(state_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", state_sha256
    ):
        raise TerraformStateVersionError("STATE_CHECKSUM_INVALID")

    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
    ):
        raise TerraformStateVersionError("STATE_SIZE_INVALID")

    state_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if state_record is None:
        raise TerraformStateVersionError("STATE_RECORD_NOT_FOUND")

    existing = db.scalar(
        select(TerraformStateVersion).where(
            TerraformStateVersion.terraform_state_record_id
            == state_record.id,
            TerraformStateVersion.version_id == version_id.strip(),
        )
    )

    if existing is not None:
        raise TerraformStateVersionError("STATE_VERSION_ALREADY_EXISTS")

    version = TerraformStateVersion(
        terraform_state_record_id=state_record.id,
        version_id=version_id.strip(),
        state_serial=state_serial,
        state_lineage=state_lineage.lower(),
        state_sha256=state_sha256.lower(),
        size_bytes=size_bytes,
    )

    try:
        db.add(version)
        db.commit()
        db.refresh(version)
    except IntegrityError as exc:
        db.rollback()
        raise TerraformStateVersionError(
            "STATE_VERSION_CREATION_CONFLICT"
        ) from exc
    except Exception:
        db.rollback()
        raise

    return version


def get_state_versions(
    db: Session,
    infrastructure_request_id: int,
) -> list[TerraformStateVersion]:
    """Retrieve historical state versions, newest first."""

    state_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if state_record is None:
        raise TerraformStateVersionError("STATE_RECORD_NOT_FOUND")

    return list(
        db.scalars(
            select(TerraformStateVersion)
            .where(
                TerraformStateVersion.terraform_state_record_id
                == state_record.id
            )
            .order_by(
                TerraformStateVersion.created_at.desc(),
                TerraformStateVersion.id.desc(),
            )
        ).all()
    )


def verify_state_content(
    state_content: bytes,
    expected_sha256: str,
) -> bool:
    """Verify state content against its recorded SHA-256 checksum."""

    if not isinstance(state_content, bytes) or not state_content:
        raise TerraformStateVersionError("STATE_CONTENT_INVALID")

    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        raise TerraformStateVersionError("STATE_CHECKSUM_INVALID")

    actual_sha256 = hashlib.sha256(state_content).hexdigest()

    return actual_sha256 == expected_sha256.lower()
