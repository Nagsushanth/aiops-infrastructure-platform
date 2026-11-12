
"""Read-only access to Terraform state stored in AWS S3."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.services.terraform.state_version_manager import (
    record_state_version,
)


class S3StateStorageError(RuntimeError):
    """Safe error raised during S3 state storage operations."""


def create_s3_client(region_name: str | None = None):
    """Create an S3 client using the configured AWS profile and region."""

    profile = os.getenv("AWS_PROFILE", "aiops-training")
    region = region_name or os.getenv(
        "TF_STATE_REGION",
        "ap-south-1",
    )

    if not isinstance(region, str) or not region.strip():
        raise S3StateStorageError("S3_REGION_INVALID")

    try:
        session = boto3.Session(
            profile_name=profile,
            region_name=region.strip(),
        )
        return session.client("s3")
    except (BotoCoreError, ClientError) as exc:
        raise S3StateStorageError(
            "S3_CLIENT_INITIALIZATION_FAILED"
        ) from exc


def read_state_object(
    client: Any,
    bucket: str,
    key: str,
    version_id: str | None = None,
) -> dict[str, Any]:
    """Read and verify the integrity of a Terraform state object."""

    if not isinstance(bucket, str) or not bucket.strip():
        raise S3StateStorageError("S3_BUCKET_INVALID")

    if not isinstance(key, str) or not key.strip():
        raise S3StateStorageError("S3_OBJECT_KEY_INVALID")

    if version_id is not None:
        if not isinstance(version_id, str) or not version_id.strip():
            raise S3StateStorageError("S3_VERSION_ID_INVALID")

    try:
        params = {
            "Bucket": bucket.strip(),
            "Key": key.strip(),
        }

        if version_id is not None:
            params["VersionId"] = version_id

        response = client.get_object(**params)
        body = response["Body"]

        try:
            content = body.read()
        finally:
            body.close()

        if not content:
            raise S3StateStorageError("S3_STATE_CONTENT_EMPTY")

        content_length = response.get("ContentLength")

        if (
            not isinstance(content_length, int)
            or isinstance(content_length, bool)
            or content_length < 0
        ):
            raise S3StateStorageError(
                "S3_STATE_CONTENT_LENGTH_INVALID"
            )

        if content_length != len(content):
            raise S3StateStorageError("S3_STATE_SIZE_MISMATCH")

        checksum = hashlib.sha256(content).hexdigest()

        return {
            "content": content,
            "version_id": response.get("VersionId"),
            "etag": response.get("ETag"),
            "last_modified": response.get("LastModified"),
            "content_length": content_length,
            "sha256": checksum,
        }

    except S3StateStorageError:
        raise
    except ClientError as exc:
        raise S3StateStorageError(
            "S3_STATE_READ_FAILED"
        ) from exc
    except (BotoCoreError, OSError, KeyError, TypeError) as exc:
        raise S3StateStorageError(
            "S3_STATE_READ_FAILED"
        ) from exc


def list_state_object_versions(
    client: Any,
    bucket: str,
    key: str,
) -> list[dict[str, Any]]:
    """List all historical versions of a Terraform state object."""

    if not isinstance(bucket, str) or not bucket.strip():
        raise S3StateStorageError("S3_BUCKET_INVALID")

    if not isinstance(key, str) or not key.strip():
        raise S3StateStorageError("S3_OBJECT_KEY_INVALID")

    try:
        paginator = client.get_paginator("list_object_versions")
        versions = []

        for page in paginator.paginate(
            Bucket=bucket.strip(),
            Prefix=key.strip(),
        ):
            for item in page.get("Versions", []):
                if item.get("Key") != key.strip():
                    continue

                versions.append(
                    {
                        "version_id": item["VersionId"],
                        "is_latest": item["IsLatest"],
                        "size_bytes": item["Size"],
                        "last_modified": item["LastModified"],
                    }
                )

        return versions

    except ClientError as exc:
        raise S3StateStorageError(
            "S3_STATE_VERSION_LIST_FAILED"
        ) from exc
    except (BotoCoreError, KeyError, TypeError) as exc:
        raise S3StateStorageError(
            "S3_STATE_VERSION_LIST_FAILED"
        ) from exc

SAFE_INVENTORY_TAGS = {
    "name",
    "environment",
    "project",
    "owner",
    "managedby",
    "costcenter",
    "application",
    "department",
    "team",
}

SENSITIVE_TAG_PARTS = {
    "secret",
    "password",
    "token",
    "credential",
    "private",
    "accesskey",
    "apikey",
}


def _safe_inventory_tags(attributes: dict) -> dict[str, str]:
    """Extract a limited set of non-sensitive inventory tags."""

    tags = attributes.get("tags")

    if not isinstance(tags, dict):
        tags = attributes.get("tags_all")

    if not isinstance(tags, dict):
        return {}

    safe_tags = {}

    for key, value in tags.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue

        normalized_key = key.lower().replace("-", "").replace("_", "")

        if normalized_key not in SAFE_INVENTORY_TAGS:
            continue

        if any(part in normalized_key for part in SENSITIVE_TAG_PARTS):
            continue

        if len(key) > 100 or len(value) > 256:
            continue

        if any(ord(character) < 32 for character in value):
            continue

        safe_tags[key] = value

    return safe_tags


def _terraform_resource_address(
    resource: dict,
    index_key: str | int | None,
) -> str:
    """Build a Terraform resource instance address."""

    mode = resource["mode"]
    resource_type = resource["type"]
    resource_name = resource["name"]
    module = resource.get("module")

    prefix = ""

    if module:
        prefix = f"{module}."

    if mode == "data":
        prefix += "data."

    address = f"{prefix}{resource_type}.{resource_name}"

    if isinstance(index_key, bool):
        raise ValueError("Invalid Terraform instance key.")

    if isinstance(index_key, int):
        address += f"[{index_key}]"
    elif isinstance(index_key, str):
        address += f"[{json.dumps(index_key, ensure_ascii=False)}]"

    return address


def _parse_state_resources(resources: Any) -> list[dict[str, Any]]:
    """Extract safe inventory metadata from Terraform state resources."""

    if not isinstance(resources, list):
        raise ValueError("Invalid Terraform resources collection.")

    inventory = []
    seen_addresses = set()

    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("Invalid Terraform resource.")

        mode = resource.get("mode")
        resource_type = resource.get("type")
        resource_name = resource.get("name")
        provider = resource.get("provider")
        module = resource.get("module")

        if mode not in {"managed", "data"}:
            raise ValueError("Invalid Terraform resource mode.")

        for value in (resource_type, resource_name, provider):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Invalid Terraform resource metadata.")

        if (
            module is not None
            and (
                not isinstance(module, str)
                or not module.startswith("module.")
            )
        ):
            raise ValueError("Invalid Terraform module address.")

        instances = resource.get("instances")

        if not isinstance(instances, list):
            raise ValueError("Invalid Terraform resource instances.")

        for instance in instances:
            if not isinstance(instance, dict):
                raise ValueError("Invalid Terraform resource instance.")

            # Deposed instances represent old objects retained during
            # replacement. They are not the current inventory entry.
            if instance.get("deposed") is not None:
                continue

            index_key = instance.get("index_key")

            if (
                index_key is not None
                and (
                    isinstance(index_key, bool)
                    or not isinstance(index_key, (str, int))
                )
            ):
                raise ValueError("Invalid Terraform instance key.")

            attributes = instance.get("attributes", {})

            if not isinstance(attributes, dict):
                raise ValueError("Invalid Terraform resource attributes.")

            dependencies = instance.get("dependencies", [])

            if (
                not isinstance(dependencies, list)
                or any(
                    not isinstance(dependency, str)
                    or not dependency.strip()
                    for dependency in dependencies
                )
            ):
                raise ValueError("Invalid Terraform dependencies.")

            address = _terraform_resource_address(
                resource,
                index_key,
            )

            if address in seen_addresses:
                raise ValueError("Duplicate Terraform resource address.")

            seen_addresses.add(address)

            resource_id = attributes.get("id")

            if (
                not isinstance(resource_id, str)
                or not resource_id.strip()
                or len(resource_id) > 256
                or any(ord(character) < 32 for character in resource_id)
            ):
                resource_id = None

            instance_status = instance.get("status", "unknown")

            if not isinstance(instance_status, str):
                raise ValueError("Invalid Terraform instance status.")

            inventory.append(
                {
                    "resource_address": address,
                    "resource_type": resource_type,
                    "resource_name": resource_name,
                    "provider": provider,
                    "mode": mode,
                    "module": module,
                    "instance_key": index_key,
                    "resource_id": resource_id,
                    "tags": _safe_inventory_tags(attributes),
                    "dependencies": sorted(set(dependencies)),
                    "instance_status": instance_status,
                }
            )

    return inventory


def parse_terraform_state(
    state_content: bytes,
) -> dict[str, Any]:
    """Parse Terraform state and extract safe resource inventory metadata."""

    if not isinstance(state_content, bytes) or not state_content:
        raise S3StateStorageError("S3_STATE_CONTENT_INVALID")

    try:
        state = json.loads(state_content)

        if not isinstance(state, dict):
            raise ValueError("State must be a JSON object.")

        version = state.get("version")

        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != 4
        ):
            raise ValueError("Unsupported Terraform state format.")

        serial = state.get("serial")

        if (
            not isinstance(serial, int)
            or isinstance(serial, bool)
            or serial < 0
        ):
            raise ValueError("Invalid Terraform state serial.")

        lineage = state.get("lineage")

        if not isinstance(lineage, str):
            raise ValueError("Invalid Terraform state lineage.")

        parsed_lineage = uuid.UUID(lineage)

        if str(parsed_lineage) != lineage.lower():
            raise ValueError("Non-canonical Terraform state lineage.")

        resources = _parse_state_resources(state.get("resources"))

        return {
            "serial": serial,
            "lineage": lineage.lower(),
            "resources": resources,
        }

    except (
        ValueError,
        TypeError,
        UnicodeDecodeError,
        AttributeError,
    ) as exc:
        raise S3StateStorageError(
            "S3_STATE_JSON_INVALID"
        ) from exc


def sync_s3_state_versions(
    db: Session,
    client: Any,
    infrastructure_request_id: int,
    bucket: str,
    key: str,
) -> dict[str, int]:
    """Synchronize the current versioned S3 Terraform state into PostgreSQL."""

    from backend.app.services.terraform.state_version_manager import (
        TerraformStateVersionError,
    )

    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise S3StateStorageError("STATE_REQUEST_ID_INVALID")

    if not isinstance(bucket, str) or not bucket.strip():
        raise S3StateStorageError("S3_BUCKET_INVALID")

    if not isinstance(key, str) or not key.strip():
        raise S3StateStorageError("S3_OBJECT_KEY_INVALID")

    bucket = bucket.strip()
    key = key.strip()

    state_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if state_record is None:
        raise S3StateStorageError("STATE_RECORD_NOT_FOUND")

    if (
        state_record.backend_type != "s3"
        or state_record.state_bucket != bucket
        or state_record.state_key != key
    ):
        raise S3StateStorageError("S3_STATE_LOCATION_MISMATCH")

    try:
        # Read the current object and its VersionId in one S3 request.
        state = read_state_object(client, bucket, key)

        version_id = state.get("version_id")
        if not isinstance(version_id, str) or not version_id.strip():
            raise S3StateStorageError("S3_STATE_VERSION_ID_MISSING")

        content = state.get("content")
        if not isinstance(content, bytes) or not content:
            raise S3StateStorageError("S3_STATE_CONTENT_EMPTY")

        if state.get("content_length") != len(content):
            raise S3StateStorageError("S3_STATE_SIZE_MISMATCH")

        metadata = parse_terraform_state(content)

        try:
            record_state_version(
                db,
                infrastructure_request_id,
                version_id=version_id,
                state_serial=metadata["serial"],
                state_lineage=metadata["lineage"],
                state_sha256=state["sha256"],
                size_bytes=len(content),
            )
            recorded = 1
            skipped = 0

        except TerraformStateVersionError as exc:
            error_code = exc.args[0] if exc.args else None

            if error_code == "STATE_VERSION_ALREADY_EXISTS":
                recorded = 0
                skipped = 1
            else:
                raise S3StateStorageError(
                    "S3_STATE_VERSION_SYNC_FAILED"
                ) from exc

        db.commit()

        return {
            "total_versions": 1,
            "recorded": recorded,
            "skipped": skipped,
        }

    except Exception:
        db.rollback()
        raise
