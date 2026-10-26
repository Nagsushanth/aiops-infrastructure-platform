
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GatherRequirementsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(max_length=5000)


class GatherRequirementsResponse(BaseModel):
    infrastructure_request_id: int
    status: str
    question: str | None = None
    missing_field: str | None = None
    requirements: dict[str, Any]
    validation_errors: list[dict[str, Any]] | None = None
