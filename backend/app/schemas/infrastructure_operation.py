from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InfrastructureOperationCreate(BaseModel):
    """Request to start an approved infrastructure operation."""

    model_config = ConfigDict(extra="forbid")

    approval_request_id: int = Field(gt=0)
    comments: str | None = Field(default=None, max_length=5000)


class InfrastructureOperationResponse(BaseModel):
    """Public operation status; never exposes private artifact paths."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    infrastructure_request_id: int
    terraform_plan_record_id: int
    approval_request_id: int
    requester_id: int
    operation: str
    status: str
    celery_task_id: str | None
    error_code: str | None
    result_summary: dict[str, Any]
    comments: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
