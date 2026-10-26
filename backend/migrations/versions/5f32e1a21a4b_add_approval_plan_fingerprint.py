"""add approval plan fingerprint

Revision ID: 5f32e1a21a4b
Revises: 4ca16d5a06e9
Create Date: 2026-10-26 15:53:06.495033
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5f32e1a21a4b"
down_revision: Union[str, Sequence[str], None] = "4ca16d5a06e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the fingerprint column without fabricating values for existing approvals."""
    op.add_column(
        "approval_requests",
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Remove the fingerprint column."""
    op.drop_column("approval_requests", "plan_fingerprint")
