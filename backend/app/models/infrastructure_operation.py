
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class InfrastructureOperation(Base):
    """Track asynchronous infrastructure lifecycle operations."""

    __tablename__ = "infrastructure_operations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    infrastructure_request_id: Mapped[int] = mapped_column(
        ForeignKey("infrastructure_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    terraform_plan_record_id: Mapped[int] = mapped_column(
        ForeignKey("terraform_plan_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    approval_request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # provision or deprovision
    operation: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # queued, running, succeeded or failed
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )

    celery_task_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )

    result_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_infrastructure_operation_request_created",
            "infrastructure_request_id",
            "created_at",
        ),
    )
