
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

        checksum = hashlib.sha256(content).hexdigest()

        return {
            "content": content,
            "version_id": response.get("VersionId"),
            "etag": response.get("ETag"),
            "last_modified": response.get("LastModified"),
            "content_length": len(content),
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


def parse_terraform_state(
    state_content: bytes,
) -> dict[str, Any]:
    """Parse and validate Terraform state JSON."""

    if not isinstance(state_content, bytes) or not state_content:
        raise S3StateStorageError("S3_STATE_CONTENT_INVALID")

    try:
        state = json.loads(state_content)

        if not isinstance(state, dict):
            raise ValueError("State must be a JSON object.")

        if state.get("version") != 4:
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

        return {
            "serial": serial,
            "lineage": lineage.lower(),
        }

    except (ValueError, TypeError, UnicodeDecodeError) as exc:
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
    """Synchronize validated S3 state versions into PostgreSQL."""

    from backend.app.services.terraform.state_version_manager import (
        TerraformStateVersionError,
    )

    # Validate the infrastructure request ID.
    if (
        not isinstance(infrastructure_request_id, int)
        or isinstance(infrastructure_request_id, bool)
        or infrastructure_request_id <= 0
    ):
        raise S3StateStorageError("STATE_REQUEST_ID_INVALID")

    # Validate the S3 bucket and object key.
    if not isinstance(bucket, str) or not bucket.strip():
        raise S3StateStorageError("S3_BUCKET_INVALID")

    if not isinstance(key, str) or not key.strip():
        raise S3StateStorageError("S3_OBJECT_KEY_INVALID")

    bucket = bucket.strip()
    key = key.strip()

    # Verify that this request has a registered Terraform state record.
    state_record = db.scalar(
        select(TerraformStateRecord).where(
            TerraformStateRecord.infrastructure_request_id
            == infrastructure_request_id
        )
    )

    if state_record is None:
        raise S3StateStorageError("STATE_RECORD_NOT_FOUND")

    # Never synchronize a different bucket or state object.
    if (
        state_record.backend_type != "s3"
        or state_record.state_bucket != bucket
        or state_record.state_key != key
    ):
        raise S3StateStorageError("S3_STATE_LOCATION_MISMATCH")

    # Retrieve the versions belonging to this exact state object.
    versions = list_state_object_versions(
        client,
        bucket,
        key,
    )

    recorded = 0
    skipped = 0

    try:
        for version in versions:
            # Read the exact historical S3 version.
            state = read_state_object(
                client,
                bucket,
                key,
                version_id=version["version_id"],
            )

            # Verify the downloaded content size.
            if state["content_length"] != version["size_bytes"]:
                raise S3StateStorageError("S3_STATE_SIZE_MISMATCH")

            # Validate Terraform state JSON and extract metadata.
            metadata = parse_terraform_state(state["content"])

            try:
                record_state_version(
                    db,
                    infrastructure_request_id,
                    version_id=version["version_id"],
                    state_serial=metadata["serial"],
                    state_lineage=metadata["lineage"],
                    state_sha256=state["sha256"],
                    size_bytes=state["content_length"],
                )

                recorded += 1

            except TerraformStateVersionError as exc:
                error_code = exc.args[0] if exc.args else None

                if error_code == "STATE_VERSION_ALREADY_EXISTS":
                    skipped += 1
                    continue

                raise S3StateStorageError(
                    "S3_STATE_VERSION_SYNC_FAILED"
                ) from exc

        # Commit only after every version has been processed successfully.
        db.commit()

    except Exception:
        # Roll back the entire batch if any version fails.
        db.rollback()
        raise

    return {
        "total_versions": len(versions),
        "recorded": recorded,
        "skipped": skipped,
    }
