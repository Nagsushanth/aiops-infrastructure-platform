
"""Synchronize sanitized Terraform resources into PostgreSQL."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.infrastructure_resource import InfrastructureResource
from backend.app.models.terraform_state import TerraformStateRecord

class InfrastructureInventoryError(RuntimeError):
    """Safe error raised during inventory synchronization."""


def sync_infrastructure_inventory(
    db: Session,
    infrastructure_request_id: int,
    state_serial: int,
    resources: list[dict],
    state_version_id: str | None = None,
    state_lineage: str | None = None,
    state_sha256: str | None = None,
) -> dict[str, int]:
    """Synchronize resources from a validated Terraform state."""

    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise InfrastructureInventoryError("STATE_REQUEST_ID_INVALID")

    if (
        not isinstance(state_serial, int)
        or isinstance(state_serial, bool)
        or state_serial < 0
    ):
        raise InfrastructureInventoryError("STATE_SERIAL_INVALID")

    if not isinstance(resources, list):
        raise InfrastructureInventoryError("INVENTORY_RESOURCES_INVALID")

    if state_version_id is not None and (
        not isinstance(state_version_id, str)
        or not state_version_id.strip()
        or len(state_version_id) > 1024
    ):
        raise InfrastructureInventoryError("STATE_VERSION_ID_INVALID")

    if state_lineage is not None and (
        not isinstance(state_lineage, str)
        or not state_lineage.strip()
        or len(state_lineage) > 64
    ):
        raise InfrastructureInventoryError("STATE_LINEAGE_INVALID")

    if state_sha256 is not None and (
        not isinstance(state_sha256, str)
        or len(state_sha256) != 64
        or any(char not in "0123456789abcdef" for char in state_sha256.lower())
    ):
        raise InfrastructureInventoryError("STATE_SHA256_INVALID")

    # Validate the complete input before modifying inventory rows.
    required_fields = {
        "resource_address",
        "resource_type",
        "resource_name",
        "provider",
        "mode",
        "module",
        "instance_key",
        "resource_id",
        "tags",
        "dependencies",
        "instance_status",
    }

    seen_addresses = set()

    for resource in resources:
        if not isinstance(resource, dict) or not required_fields.issubset(
            resource
        ):
            raise InfrastructureInventoryError(
                "INVENTORY_RESOURCE_INVALID"
            )

        address = resource["resource_address"]

        if (
            not isinstance(address, str)
            or not address.strip()
            or len(address) > 512
            or address in seen_addresses
        ):
            raise InfrastructureInventoryError(
                "INVENTORY_RESOURCE_ADDRESS_INVALID"
            )

        seen_addresses.add(address)

    try:
        # Lock the parent request to serialize concurrent inventory syncs.
        request = db.scalar(
            select(InfrastructureRequest)
            .where(InfrastructureRequest.id == infrastructure_request_id)
            .with_for_update()
        )

        if request is None:
            raise InfrastructureInventoryError("STATE_REQUEST_NOT_FOUND")

        # Lock the Terraform state record so serial updates are protected.
        state_record = db.scalar(
            select(TerraformStateRecord)
            .where(
                TerraformStateRecord.infrastructure_request_id
                == infrastructure_request_id
            )
            .with_for_update()
        )

        if state_record is None:
            raise InfrastructureInventoryError("STATE_RECORD_NOT_FOUND")

        existing_rows = list(
            db.scalars(
                select(InfrastructureResource)
                .where(
                    InfrastructureResource.infrastructure_request_id
                    == infrastructure_request_id
                )
                .with_for_update()
            ).all()
        )

        existing_by_address = {
            row.resource_address: row for row in existing_rows
        }

        # Use the state record as the authoritative serial watermark.
        # Also check inventory rows for protection of existing records.
        latest_serial = state_record.state_serial

        if existing_rows:
            inventory_latest_serial = max(
                row.state_serial for row in existing_rows
            )

            if (
                latest_serial is None
                or inventory_latest_serial > latest_serial
            ):
                latest_serial = inventory_latest_serial

        if latest_serial is not None and state_serial < latest_serial:
            raise InfrastructureInventoryError(
                "INVENTORY_STATE_SERIAL_STALE"
            )

        now = datetime.now(timezone.utc)

        created = 0
        updated = 0
        unchanged = 0
        marked_absent = 0

        current_addresses = set(seen_addresses)

        metadata_fields = {
            "resource_type",
            "resource_name",
            "provider",
            "mode",
            "module",
            "instance_key",
            "resource_id",
            "tags",
            "dependencies",
            "instance_status",
        }

        for resource in resources:
            address = resource["resource_address"]
            existing = existing_by_address.get(address)

            values = {
                "resource_type": resource["resource_type"],
                "resource_name": resource["resource_name"],
                "provider": resource["provider"],
                "mode": resource["mode"],
                "module": resource["module"],
                "instance_key": resource["instance_key"],
                "resource_id": resource["resource_id"],
                "tags": resource["tags"],
                "dependencies": resource["dependencies"],
                "instance_status": resource["instance_status"],
                "state_serial": state_serial,
                "is_present": True,
                "last_seen_at": now,
            }

            if existing is None:
                db.add(
                    InfrastructureResource(
                        infrastructure_request_id=infrastructure_request_id,
                        resource_address=address,
                        **values,
                    )
                )
                created += 1
                continue

            changed = (
                not existing.is_present
                or any(
                    getattr(existing, field) != values[field]
                    for field in metadata_fields
                )
            )

            # Refresh synchronization metadata even when resource data
            # has not changed.
            for field, value in values.items():
                setattr(existing, field, value)

            if changed:
                updated += 1
            else:
                unchanged += 1

        # Retain historical records instead of deleting absent resources.
        for row in existing_rows:
            if (
                row.is_present
                and row.resource_address not in current_addresses
            ):
                row.is_present = False
                row.state_serial = state_serial
                row.updated_at = now
                marked_absent += 1

        # Persist the serial even when the state contains zero resources.
        state_record.state_version_id = state_version_id
        state_record.state_serial = state_serial
        state_record.state_lineage = state_lineage
        state_record.state_sha256 = state_sha256

        db.commit()

        return {
            "total_resources": len(resources),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "marked_absent": marked_absent,
        }

    except InfrastructureInventoryError:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise InfrastructureInventoryError(
            "INVENTORY_SYNC_FAILED"
        ) from exc
