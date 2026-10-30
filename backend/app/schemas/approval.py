
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    infrastructure_request_id: int = Field(gt=0)
    terraform_plan_record_id: int = Field(gt=0)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    comments: str | None = Field(default=None, max_length=5000)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    infrastructure_request_id: int
    terraform_plan_record_id: int | None
    requester_id: int
    reviewer_id: int | None
    status: str
    plan_summary: dict[str, Any]
    reviewer_comments: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    approval_request_id: int
    actor_id: int
    event_type: str
    previous_status: str | None
    new_status: str
    comments: str | None
    event_metadata: dict[str, Any]
    created_at: datetime



class ApprovalStageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    comments: str | None = Field(default=None, max_length=5000)

    # Only applicable to the cost review.
    manual_cost_override: bool = False



class ApprovalStageReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    approval_request_id: int
    stage: str
    status: str
    assigned_reviewer_id: int | None
    decided_by_id: int | None
    comments: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime
