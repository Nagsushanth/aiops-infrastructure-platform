
import pytest

from backend.app.services.ai.resource_sizing import (
    recommend_resource_sizing,
)


@pytest.mark.parametrize(
    ("description", "expected_vcpus", "expected_memory"),
    [
        ("I need a small development app", 2, 4),
        ("Build a lightweight web application", 2, 4),
        ("I need a medium workload", 2, 8),
        ("This application is CPU intensive", 4, 8),
        ("This workload is memory intensive", 2, 16),
    ],
)
def test_recommends_sizing_for_known_workloads(
    description,
    expected_vcpus,
    expected_memory,
):
    result = recommend_resource_sizing(description)

    assert result is not None
    assert result["vcpus"] == expected_vcpus
    assert result["memory_gb"] == expected_memory


def test_unknown_workload_returns_no_recommendation():
    result = recommend_resource_sizing(
        "I need an infrastructure resource"
    )

    assert result is None


def test_empty_workload_returns_no_recommendation():
    assert recommend_resource_sizing("") is None


def test_recommendation_does_not_modify_input():
    description = "I need a small development app"

    recommend_resource_sizing(description)

    assert description == "I need a small development app"


def test_recommendation_contains_confirmation_message():
    result = recommend_resource_sizing(
        "I need a small development app"
    )

    assert "confirm" in result["message"].lower()
    assert "2 vCPUs" in result["message"]
    assert "4 GB RAM" in result["message"]
