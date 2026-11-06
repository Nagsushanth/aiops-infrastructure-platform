
"""Manage persistent Terraform state metadata."""

import hashlib
import re
import uuid
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.terraform_state import TerraformStateRecord


class TerraformStateError(RuntimeError):
    """Safe error raised during Terraform state metadata operations."""


VALID_BACKEND_TYPES = {"s3"}
VALID_STATUSES = {
    "pending",
    "initializing",
    "active",
    "failed",
    "archived",
}
VALID_STATE_TRANSITIONS = {
    "pending": {"initializing", "failed"},
    "initializing": {"initializing", "active", "failed"},
    "active": {"active", "archived"},
    "failed": {"initializing", "archived"},
    "archived": set(),
}


def create_state_record(
    db: Session,
    infrastructure_request_id: int,
    state_bucket: str,
    state_key: str,
    state_region: str,
    backend_type: str = "s3",
) -> TerraformStateRecord:
    """Create one state metadata record for an infrastructure request."""

    if backend_type not in VALID_BACKEND_TYPES:
        raise TerraformStateError("STATE_BACKEND_TYPE_INVALID")

    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise TerraformStateError("STATE_REQUEST_ID_INVALID")

    if not all(
        isinstance(value, str) and value.strip()
        for value in (state_bucket, state_key, state_region)
    ):
        raise TerraformStateError("STATE_BACKEND_CONFIGURATION_INVALID")

    infrastructure_request = db.get(
        InfrastructureRequest,
        infrastructure_request_id,
    )

    if infrastructure_request is None:
        raise TerraformStateError("INFRASTRUCTURE_REQUEST_NOT_FOUND")

    existing_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if existing_record is not None:
        raise TerraformStateError("STATE_RECORD_ALREADY_EXISTS")

    record = TerraformStateRecord(
        infrastructure_request_id=infrastructure_request_id,
        backend_type=backend_type,
        state_bucket=state_bucket.strip(),
        state_key=state_key.strip(),
        state_region=state_region.strip(),
        status="pending",
    )

    try:
        db.add(record)
        db.commit()
        db.refresh(record)
    except IntegrityError as exc:
        db.rollback()
        raise TerraformStateError(
            "STATE_RECORD_CREATION_CONFLICT"
        ) from exc
    except Exception:
        db.rollback()
        raise

    return record


def get_state_record(
    db: Session,
    infrastructure_request_id: int,
) -> TerraformStateRecord | None:
    """Retrieve state metadata by infrastructure request ID."""

    return db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )


def update_state_metadata(
    db: Session,
    infrastructure_request_id: int,
    *,
    state_version_id: str | None = None,
    state_serial: int | None = None,
    state_lineage: str | None = None,
    state_sha256: str | None = None,
    status: str | None = None,
) -> TerraformStateRecord:
    """Update validated state metadata."""

    record = get_state_record(db, infrastructure_request_id)

    if record is None:
        raise TerraformStateError("STATE_RECORD_NOT_FOUND")

    if state_version_id is not None:
        if not isinstance(state_version_id, str) or not state_version_id.strip():
            raise TerraformStateError("STATE_VERSION_ID_INVALID")
        record.state_version_id = state_version_id.strip()

    if state_serial is not None:
        if not isinstance(state_serial, int) or isinstance(state_serial, bool) or state_serial < 0:
            raise TerraformStateError("STATE_SERIAL_INVALID")
        record.state_serial = state_serial

    if state_lineage is not None:
        try:
            parsed_lineage = uuid.UUID(state_lineage)

            if str(parsed_lineage) != state_lineage.lower():
                raise ValueError("Non-canonical UUID")

        except (ValueError, TypeError, AttributeError):
            raise TerraformStateError("STATE_LINEAGE_INVALID")

        record.state_lineage = state_lineage.lower()

    if state_sha256 is not None:
        if not isinstance(state_sha256, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", state_sha256
        ):
            raise TerraformStateError("STATE_CHECKSUM_INVALID")
        record.state_sha256 = state_sha256.lower()

    if status is not None:
        if not isinstance(status, str) or status not in VALID_STATUSES:
            raise TerraformStateError("STATE_STATUS_INVALID")

        if record.status not in VALID_STATE_TRANSITIONS:
            raise TerraformStateError("STATE_STATUS_INVALID")

        allowed_transitions = VALID_STATE_TRANSITIONS[record.status]

        if status not in allowed_transitions:
            raise TerraformStateError("STATE_TRANSITION_INVALID")

        record.status = status

    try:
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        raise

    return record


def calculate_state_sha256(state_bytes: bytes) -> str:
    """Calculate SHA-256 for Terraform state content."""

    if not isinstance(state_bytes, bytes) or not state_bytes:
        raise TerraformStateError("STATE_CONTENT_INVALID")

    return hashlib.sha256(state_bytes).hexdigest()
