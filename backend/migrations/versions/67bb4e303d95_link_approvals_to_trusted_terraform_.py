"""link approvals to trusted terraform plans

Revision ID: 67bb4e303d95
Revises: 8d2f6a91c430
Create Date: 2026-10-27 14:54:58.357793

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67bb4e303d95'
down_revision: Union[str, Sequence[str], None] = '8d2f6a91c430'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "approval_requests",
        sa.Column("terraform_plan_record_id", sa.Integer(), nullable=True),
    )

    op.create_index(
        "ix_approval_requests_terraform_plan_record_id",
        "approval_requests",
        ["terraform_plan_record_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_approval_requests_terraform_plan_record_id",
        "approval_requests",
        "terraform_plan_records",
        ["terraform_plan_record_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_approval_requests_terraform_plan_record_id",
        "approval_requests",
        type_="foreignkey",
    )

    op.drop_index(
        "ix_approval_requests_terraform_plan_record_id",
        table_name="approval_requests",
    )

    op.drop_column("approval_requests", "terraform_plan_record_id")
