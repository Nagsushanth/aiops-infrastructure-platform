"""Deterministic workload-based infrastructure sizing recommendations."""

import re
from typing import Any


SIZING_PROFILES: dict[str, dict[str, Any]] = {
    "small": {
        "vcpus": 2,
        "memory_gb": 4,
        "description": "Small or lightweight application",
        "keywords": (
            "small development web application",
            "small development web app",
            "small application",
            "small app",
            "lightweight application",
            "lightweight web application",
            "lightweight app",
            "development application",
            "development app",
            "basic web application",
            "basic web app",
        ),
    },
    "medium": {
        "vcpus": 2,
        "memory_gb": 8,
        "description": "Medium-sized application",
        "keywords": (
            "medium application",
            "medium app",
            "medium workload",
            "moderate workload",
            "moderate application",
        ),
    },
    "cpu_intensive": {
        "vcpus": 4,
        "memory_gb": 8,
        "description": "CPU-intensive workload",
        "keywords": (
            "cpu intensive",
            "compute intensive",
            "compute heavy",
            "high cpu",
            "processing intensive",
        ),
    },
    "memory_intensive": {
        "vcpus": 2,
        "memory_gb": 16,
        "description": "Memory-intensive workload",
        "keywords": (
            "memory intensive",
            "memory heavy",
            "high memory",
            "large memory",
            "memory hungry",
        ),
    },
}


def recommend_resource_sizing(
    workload_description: str,
) -> dict[str, Any] | None:
    """Return an indicative sizing recommendation for a recognized workload.

    Explicit user-provided CPU and memory values are handled separately
    by requirement gathering and must never be overwritten by this service.
    """
    if not isinstance(workload_description, str):
        return None

    normalized = re.sub(
        r"[^a-z0-9\s-]",
        " ",
        workload_description.lower(),
    )
    normalized = " ".join(normalized.split())

    for profile_name, profile in SIZING_PROFILES.items():
        for keyword in profile["keywords"]:
            if keyword in normalized:
                return {
                    "profile": profile_name,
                    "description": profile["description"],
                    "vcpus": profile["vcpus"],
                    "memory_gb": profile["memory_gb"],
                    "message": (
                        f"For this workload, the indicative recommendation "
                        f"is {profile['vcpus']} vCPUs and "
                        f"{profile['memory_gb']} GB RAM. "
                        "Please confirm these values or provide your "
                        "preferred CPU and memory."
                    ),
                }

    return None
