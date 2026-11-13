
import pytest
from botocore.exceptions import ClientError

from backend.app.services.terraform.resource_health import (
    ResourceHealthError,
    get_ec2_resource_health,
)


class FakeEC2Client:
    def __init__(
        self,
        instances=None,
        statuses=None,
        error=None,
    ):
        self.instances = instances or []
        self.statuses = statuses or []
        self.error = error
        self.describe_instances_calls = []
        self.describe_status_calls = []

    def describe_instances(self, **kwargs):
        self.describe_instances_calls.append(kwargs)

        if self.error:
            raise self.error

        return {
            "Reservations": [
                {"Instances": self.instances}
            ]
        }

    def describe_instance_status(self, **kwargs):
        self.describe_status_calls.append(kwargs)

        if self.error:
            raise self.error

        return {"InstanceStatuses": self.statuses}


def test_healthy_ec2_instance():
    client = FakeEC2Client(
        instances=[
            {
                "InstanceId": "i-1234567890abcdef0",
                "State": {"Name": "running"},
            }
        ],
        statuses=[
            {
                "InstanceId": "i-1234567890abcdef0",
                "SystemStatus": {"Status": "ok"},
                "InstanceStatus": {"Status": "ok"},
            }
        ],
    )

    result = get_ec2_resource_health(
        client,
        ["i-1234567890abcdef0"],
    )

    assert result == [
        {
            "instance_id": "i-1234567890abcdef0",
            "lifecycle_state": "running",
            "health_status": "healthy",
            "system_status": "ok",
            "instance_status": "ok",
        }
    ]

    assert client.describe_instances_calls
    assert client.describe_status_calls


def test_impaired_ec2_instance():
    client = FakeEC2Client(
        instances=[
            {
                "InstanceId": "i-1234567890abcdef0",
                "State": {"Name": "running"},
            }
        ],
        statuses=[
            {
                "InstanceId": "i-1234567890abcdef0",
                "SystemStatus": {"Status": "impaired"},
                "InstanceStatus": {"Status": "ok"},
            }
        ],
    )

    result = get_ec2_resource_health(
        client,
        ["i-1234567890abcdef0"],
    )

    assert result[0]["health_status"] == "impaired"


def test_stopped_instance_is_not_marked_unhealthy():
    client = FakeEC2Client(
        instances=[
            {
                "InstanceId": "i-1234567890abcdef0",
                "State": {"Name": "stopped"},
            }
        ]
    )

    result = get_ec2_resource_health(
        client,
        ["i-1234567890abcdef0"],
    )

    assert result[0]["lifecycle_state"] == "stopped"
    assert result[0]["health_status"] == "not_running"


def test_missing_instance_returns_unknown():
    client = FakeEC2Client()

    result = get_ec2_resource_health(
        client,
        ["i-1234567890abcdef0"],
    )

    assert result[0]["lifecycle_state"] == "not_found"
    assert result[0]["health_status"] == "unknown"


def test_invalid_instance_ids_are_not_sent_to_aws():
    client = FakeEC2Client()

    result = get_ec2_resource_health(
        client,
        [
            "invalid-id",
            "",
            None,
            "i-1234567890abcdef0",
        ],
    )

    assert len(result) == 1
    assert result[0]["instance_id"] == "i-1234567890abcdef0"
    assert client.describe_instances_calls[0]["InstanceIds"] == [
        "i-1234567890abcdef0"
    ]


def test_empty_instance_list_does_not_call_aws():
    client = FakeEC2Client()

    result = get_ec2_resource_health(client, [])

    assert result == []
    assert client.describe_instances_calls == []
    assert client.describe_status_calls == []


def test_invalid_input_type_is_rejected():
    client = FakeEC2Client()

    with pytest.raises(
        ResourceHealthError,
        match="INSTANCE_IDS_INVALID",
    ):
        get_ec2_resource_health(client, "i-1234567890abcdef0")


def test_aws_client_error_is_safely_wrapped():
    error = ClientError(
        {
            "Error": {
                "Code": "UnauthorizedOperation",
                "Message": "Access denied",
            }
        },
        "DescribeInstances",
    )

    client = FakeEC2Client(error=error)

    with pytest.raises(
        ResourceHealthError,
        match="EC2_HEALTH_RETRIEVAL_FAILED",
    ):
        get_ec2_resource_health(
            client,
            ["i-1234567890abcdef0"],
        )
