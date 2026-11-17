from datetime import datetime
from typing import Any, Literal
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

class TerraformStateVersionResponse(BaseModel):
    """Safe metadata for one Terraform state version."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: str
    state_serial: int
    state_lineage: str
    state_sha256: str
    size_bytes: int
    created_at: datetime


class TerraformStateVersionSyncResponse(BaseModel):
    """Result of synchronizing S3 state-version history."""

    infrastructure_request_id: int
    total_versions: int
    recorded: int
    skipped: int
    versions: list[TerraformStateVersionResponse]

class InfrastructureInventorySyncResponse(BaseModel):
    """Result of synchronizing Terraform resource inventory."""

    infrastructure_request_id: int
    total_resources: int
    created: int
    updated: int
    unchanged: int
    marked_absent: int

class InfrastructureDeprovisionCreate(BaseModel):
    """Request to destroy infrastructure after explicit confirmation."""

    model_config = ConfigDict(extra="forbid")

    approval_request_id: int = Field(gt=0)
    confirm_destroy: Literal[True]
    comments: str | None = Field(default=None, max_length=5000)

class InfrastructureRecoveryResolution(BaseModel):
    """Admin decision to resolve a manual infrastructure recovery review."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["resolved"]
    verification_scope: Literal[
        "no_changes_confirmed",
        "state_and_cloud_reconciled",
    ]
    evidence: str = Field(min_length=20, max_length=4000)
    comments: str = Field(min_length=10, max_length=2000)