from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class InfrastructureResource(Base):
    """Store sanitized resource inventory extracted from Terraform state."""

    __tablename__ = "infrastructure_resources"

    id: Mapped[int] = mapped_column(primary_key=True)

    infrastructure_request_id: Mapped[int] = mapped_column(
        ForeignKey("infrastructure_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )

    resource_address: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    resource_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    provider: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    module: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    # Terraform count/for_each instance key: integer, string or null.
    instance_key: Mapped[Any | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Retain only the sanitized resource identifier, never raw attributes.
    resource_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    tags: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    dependencies: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    instance_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )

    state_serial: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # A resource missing from a newer state remains in inventory history.
    is_present: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
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
            "resource_address",
            name="uq_infrastructure_resource_request_address",
        ),
        Index(
            "ix_infrastructure_resource_request_type",
            "infrastructure_request_id",
            "resource_type",
        ),
        Index(
            "ix_infrastructure_resource_request_status",
            "infrastructure_request_id",
            "is_present",
        ),
    )