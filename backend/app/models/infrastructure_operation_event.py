from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class InfrastructureOperationEvent(Base):
    """Audit events for infrastructure lifecycle operations."""

    __tablename__ = "infrastructure_operation_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    operation_id: Mapped[int] = mapped_column(
        ForeignKey("infrastructure_operations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # A system event has no human actor.
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # user or system
    actor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )