
from logging.config import fileConfig
import os
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL
from dotenv import load_dotenv

from backend.app.models.base import Base
from backend.app.models.approval import ApprovalAuditEvent, ApprovalRequest
from backend.app.models.conversation import Conversation
from backend.app.models.messages import Message
from backend.app.models.message_attachment import MessageAttachment
from backend.app.models.infrastructure_request import InfrastructureRequest
from backend.app.models.user import User
from backend.app.models.infrastructure_operation import InfrastructureOperation
from backend.app.models.terraform_state import TerraformStateRecord
from backend.app.models.terraform_state_version import TerraformStateVersion
from backend.app.models.terraform_plan import TerraformPlanRecord
from backend.app.models.approval import (
    ApprovalAuditEvent,
    ApprovalRequest,
    ApprovalStageReview,
)
from backend.app.models.infrastructure_operation_event import (
    InfrastructureOperationEvent,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

required_settings = [
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
]

missing_settings = [
    name for name in required_settings if not os.getenv(name)
]

if missing_settings:
    raise RuntimeError(
        "Missing database settings: " + ", ".join(missing_settings)
    )

database_url = URL.create(
    drivername="postgresql+psycopg",
    username=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    host=os.environ["POSTGRES_HOST"],
    port=int(os.environ["POSTGRES_PORT"]),
    database=os.environ["POSTGRES_DB"],
)

# Pass the URL directly; do not store credentials in alembic.ini.
config.set_main_option(
    "sqlalchemy.url",
    database_url.render_as_string(hide_password=False).replace("%", "%%"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
