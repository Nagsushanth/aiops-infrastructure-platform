"""add terraform plan state snapshot metadata

Revision ID: e107b44449c8
Revises: ec4b8848c703
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e107b44449c8"
down_revision: Union[str, None] = "ec4b8848c703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "terraform_plan_records",
        sa.Column("state_version_id", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "terraform_plan_records",
        sa.Column("state_serial", sa.Integer(), nullable=True),
    )
    op.add_column(
        "terraform_plan_records",
        sa.Column("state_lineage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "terraform_plan_records",
        sa.Column("state_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("terraform_plan_records", "state_sha256")
    op.drop_column("terraform_plan_records", "state_lineage")
    op.drop_column("terraform_plan_records", "state_serial")
    op.drop_column("terraform_plan_records", "state_version_id")