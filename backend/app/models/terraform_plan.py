
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


class TerraformPlanRecord(Base):
    """Persist metadata for a plan produced by a trusted server workflow."""

    __tablename__ = "terraform_plan_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    infrastructure_request_id: Mapped[int] = mapped_column(
        ForeignKey("infrastructure_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )

    plan_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    specification_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    # Private filesystem reference; never return this through the API.
    artifact_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    # SHA-256 of the persisted binary Terraform plan.
    artifact_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Only a sanitized summary belongs here, never raw plan JSON or secrets.
    plan_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(80),
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
            "ix_terraform_plan_request_created",
            "infrastructure_request_id",
            "created_at",
        ),
    )
