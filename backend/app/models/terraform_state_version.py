"""Track historical versions of persistent Terraform state."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class TerraformStateVersion(Base):
    """Record a recoverable version of Terraform state."""

    __tablename__ = "terraform_state_versions"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    terraform_state_record_id: Mapped[int] = mapped_column(
        ForeignKey(
            "terraform_state_records.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    version_id: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    state_serial: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    state_lineage: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    state_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "terraform_state_record_id",
            "version_id",
            name="uq_terraform_state_version_id",
        ),
        Index(
            "ix_terraform_state_version_created",
            "terraform_state_record_id",
            "created_at",
        ),
    )
