"""Add model status history table.

Revision ID: 002_status_history
Revises: 001_initial
Create Date: 2026-03-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "002_status_history"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reuse the existing custom_model_status enum (do NOT recreate)
    custom_model_status = postgresql.ENUM(
        "testing",
        "prerelease",
        "lts",
        "deprecating",
        "deprecated",
        name="custom_model_status",
        create_type=False,
    )

    op.create_table(
        "custom_model_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "catalog_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_model_catalog.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("previous_status", custom_model_status, nullable=True),
        sa.Column("new_status", custom_model_status, nullable=False),
        sa.Column("changed_by", sa.String(128), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Index for fast history lookup per catalog entry
    op.create_index(
        "ix_custom_model_status_history_catalog_id",
        "custom_model_status_history",
        ["catalog_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_model_status_history_catalog_id")
    op.drop_table("custom_model_status_history")
