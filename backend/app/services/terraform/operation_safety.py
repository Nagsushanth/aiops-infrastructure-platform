
"""Safety checks shared by infrastructure APIs and workers."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.infrastructure_operation import (
    InfrastructureOperation,
)
from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)
from backend.app.models.user import User


def _has_valid_recovery_resolution(
    db: Session,
    operation_id: int,
) -> bool:
    """Return whether an admin recorded a documented recovery resolution."""

    events = db.scalars(
        select(InfrastructureOperationEvent)
        .join(
            User,
            User.id == InfrastructureOperationEvent.actor_id,
        )
        .where(
            InfrastructureOperationEvent.operation_id == operation_id,
            InfrastructureOperationEvent.event_type
            == "recovery_review_resolved",
            InfrastructureOperationEvent.new_status == "resolved",
            InfrastructureOperationEvent.actor_type == "user",
            User.role == "admin",
            User.is_active.is_(True),
        )
    ).all()

    for event in events:
        metadata = event.event_metadata or {}

        if not isinstance(metadata, dict):
            continue

        if (
            metadata.get("source") == "recovery_review_api"
            and metadata.get("decision") == "resolved"
            and metadata.get("verification_scope")
            in {
                "no_changes_confirmed",
                "state_and_cloud_reconciled",
            }
            and isinstance(metadata.get("evidence"), str)
            and len(metadata["evidence"].strip()) >= 20
            and metadata.get("reviewer_id") == event.actor_id
            and event.actor_id is not None
        ):
            return True

    return False


def has_unresolved_recovery_operation(
    db: Session,
    infrastructure_request_id: int,
    exclude_operation_id: int | None = None,
) -> bool:
    """Check whether an earlier operation still requires recovery review."""

    statement = select(InfrastructureOperation).where(
        InfrastructureOperation.infrastructure_request_id
        == infrastructure_request_id,
    )

    if exclude_operation_id is not None:
        statement = statement.where(
            InfrastructureOperation.id != exclude_operation_id
        )

    operations = db.scalars(statement).all()

    for operation in operations:
        summary = operation.result_summary or {}

        if not isinstance(summary, dict):
            continue

        requires_review = (
            summary.get("recovery_required") is True
            or summary.get("manual_review_required") is True
            or summary.get("recovery_status") == "MANUAL_REVIEW_REQUIRED"
        )

        if not requires_review:
            continue

        if not _has_valid_recovery_resolution(db, operation.id):
            return True

    return False
