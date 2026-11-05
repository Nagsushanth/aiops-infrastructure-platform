"""Track persistent Terraform state metadata."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class TerraformStateRecord(Base):
    """Track the persistent Terraform state for an infrastructure request."""

    __tablename__ = "terraform_state_records"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    infrastructure_request_id: Mapped[int] = mapped_column(
        ForeignKey(
            "infrastructure_requests.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    backend_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="s3",
        server_default="s3",
    )

    state_bucket: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    state_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    state_region: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    state_version_id: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    state_serial: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    state_lineage: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    state_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
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
        UniqueConstraint(
            "infrastructure_request_id",
            name="uq_terraform_state_infrastructure_request",
        ),
        Index(
            "ix_terraform_state_request_created",
            "infrastructure_request_id",
            "created_at",
        ),
    )
