"""Add trusted systems for keyless gateway authentication.

Revision ID: 018_trusted_system
Revises: 017_benchmark_run
"""

import sqlalchemy as sa
from alembic import op

revision = "018_trusted_system"
down_revision = "017_benchmark_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_trusted_systems",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("system_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("litellm_key", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_custom_trusted_systems_system_id",
        "custom_trusted_systems",
        ["system_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_custom_trusted_systems_system_id", table_name="custom_trusted_systems")
    op.drop_table("custom_trusted_systems")
