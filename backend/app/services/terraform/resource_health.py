
"""Read-only AWS EC2 resource health checks."""

import re
from typing import Any
import os
import boto3
from botocore.exceptions import BotoCoreError, ClientError


EC2_INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")


class ResourceHealthError(RuntimeError):
    """Safe error raised when AWS resource health cannot be retrieved."""


def get_ec2_resource_health(
    ec2_client: Any,
    instance_ids: list[str],
) -> list[dict[str, Any]]:
    """Retrieve live EC2 lifecycle state and status checks."""

    if not isinstance(instance_ids, list):
        raise ResourceHealthError("INSTANCE_IDS_INVALID")

    valid_ids = sorted({
        instance_id
        for instance_id in instance_ids
        if isinstance(instance_id, str)
        and EC2_INSTANCE_ID_PATTERN.fullmatch(instance_id)
    })

    if not valid_ids:
        return []

    try:
        response = ec2_client.describe_instances(
            InstanceIds=valid_ids,
        )
        status_response = ec2_client.describe_instance_status(
            InstanceIds=valid_ids,
            IncludeAllInstances=True,
        )
    except (BotoCoreError, ClientError) as exc:
        raise ResourceHealthError(
            "EC2_HEALTH_RETRIEVAL_FAILED"
        ) from exc

    instances_by_id = {}

    for reservation in response.get("Reservations", []):
        if not isinstance(reservation, dict):
            continue

        for instance in reservation.get("Instances", []):
            if not isinstance(instance, dict):
                continue

            instance_id = instance.get("InstanceId")
            state = instance.get("State")

            if (
                isinstance(instance_id, str)
                and instance_id in valid_ids
                and isinstance(state, dict)
            ):
                state_name = state.get("Name")

                if isinstance(state_name, str):
                    instances_by_id[instance_id] = state_name

    checks_by_id = {}

    for status in status_response.get("InstanceStatuses", []):
        if not isinstance(status, dict):
            continue

        instance_id = status.get("InstanceId")

        if not isinstance(instance_id, str) or instance_id not in valid_ids:
            continue

        system_status = status.get("SystemStatus", {})
        instance_status = status.get("InstanceStatus", {})

        checks_by_id[instance_id] = {
            "system_status": (
                system_status.get("Status")
                if isinstance(system_status, dict)
                else None
            ),
            "instance_status": (
                instance_status.get("Status")
                if isinstance(instance_status, dict)
                else None
            ),
        }

    results = []

    for instance_id in valid_ids:
        lifecycle_state = instances_by_id.get(instance_id)

        if lifecycle_state is None:
            results.append({
                "instance_id": instance_id,
                "lifecycle_state": "not_found",
                "health_status": "unknown",
                "system_status": None,
                "instance_status": None,
            })
            continue

        checks = checks_by_id.get(instance_id, {})
        system_status = checks.get("system_status")
        instance_status = checks.get("instance_status")

        if lifecycle_state != "running":
            health_status = "not_running"
        elif system_status == "impaired" or instance_status == "impaired":
            health_status = "impaired"
        elif system_status == "ok" and instance_status == "ok":
            health_status = "healthy"
        else:
            health_status = "initializing"

        results.append({
            "instance_id": instance_id,
            "lifecycle_state": lifecycle_state,
            "health_status": health_status,
            "system_status": system_status,
            "instance_status": instance_status,
        })

    return results

def create_ec2_client(region_name: str):
    """Create an authenticated, region-specific EC2 client."""

    if not isinstance(region_name, str) or not region_name.strip():
        raise ResourceHealthError("AWS_REGION_INVALID")

    profile = os.getenv("AWS_PROFILE", "aiops-training")

    try:
        session = boto3.Session(
            profile_name=profile,
            region_name=region_name.strip(),
        )
        return session.client("ec2")
    except (BotoCoreError, ClientError) as exc:
        raise ResourceHealthError(
            "EC2_CLIENT_INITIALIZATION_FAILED"
        ) from exc
