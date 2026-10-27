
"""Add trusted Terraform plan metadata records."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d2f6a91c430"
down_revision: Union[str, Sequence[str], None] = "5f32e1a21a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "terraform_plan_records",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "infrastructure_request_id",
            sa.Integer(),
            sa.ForeignKey(
                "infrastructure_requests.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "requester_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "specification_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("plan_summary", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_terraform_plan_records_id",
        "terraform_plan_records",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_terraform_plan_records_infrastructure_request_id",
        "terraform_plan_records",
        ["infrastructure_request_id"],
        unique=False,
    )
    op.create_index(
        "ix_terraform_plan_records_requester_id",
        "terraform_plan_records",
        ["requester_id"],
        unique=False,
    )
    op.create_index(
        "ix_terraform_plan_records_status",
        "terraform_plan_records",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_terraform_plan_request_created",
        "terraform_plan_records",
        ["infrastructure_request_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_terraform_plan_request_created",
        table_name="terraform_plan_records",
    )
    op.drop_index(
        "ix_terraform_plan_records_status",
        table_name="terraform_plan_records",
    )
    op.drop_index(
        "ix_terraform_plan_records_requester_id",
        table_name="terraform_plan_records",
    )
    op.drop_index(
        "ix_terraform_plan_records_infrastructure_request_id",
        table_name="terraform_plan_records",
    )
    op.drop_index(
        "ix_terraform_plan_records_id",
        table_name="terraform_plan_records",
    )
    op.drop_table("terraform_plan_records")
