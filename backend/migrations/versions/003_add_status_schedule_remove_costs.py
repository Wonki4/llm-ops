"""Add status_schedule JSON, remove cost columns and auto_deprecate_at.

Cost data now comes from LiteLLM model_info; catalog stores only
lifecycle scheduling via status_schedule JSON.

Revision ID: 003_status_schedule
Revises: 002_status_history
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "003_status_schedule"
down_revision = "002_status_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add status_schedule JSON column
    op.add_column(
        "custom_model_catalog",
        sa.Column("status_schedule", postgresql.JSON, nullable=True),
    )

    # Migrate auto_deprecate_at data into status_schedule
    op.execute(
        """
        UPDATE custom_model_catalog
        SET status_schedule = jsonb_build_object('deprecated', to_char(auto_deprecate_at, 'YYYY-MM-DD'))
        WHERE auto_deprecate_at IS NOT NULL
        """
    )

    # Drop columns no longer needed
    op.drop_index("ix_custom_model_catalog_auto_deprecate_at", table_name="custom_model_catalog")
    op.drop_column("custom_model_catalog", "auto_deprecate_at")
    op.drop_column("custom_model_catalog", "input_cost_per_token")
    op.drop_column("custom_model_catalog", "output_cost_per_token")
    op.drop_column("custom_model_catalog", "cost_info")


def downgrade() -> None:
    # Re-add removed columns
    op.add_column(
        "custom_model_catalog",
        sa.Column("cost_info", postgresql.JSON, nullable=True),
    )
    op.add_column(
        "custom_model_catalog",
        sa.Column("output_cost_per_token", sa.Float, nullable=True),
    )
    op.add_column(
        "custom_model_catalog",
        sa.Column("input_cost_per_token", sa.Float, nullable=True),
    )
    op.add_column(
        "custom_model_catalog",
        sa.Column("auto_deprecate_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_custom_model_catalog_auto_deprecate_at",
        "custom_model_catalog",
        ["auto_deprecate_at"],
    )

    # Migrate status_schedule.deprecated back to auto_deprecate_at
    op.execute(
        """
        UPDATE custom_model_catalog
        SET auto_deprecate_at = (status_schedule->>'deprecated')::timestamptz
        WHERE status_schedule->>'deprecated' IS NOT NULL
        """
    )

    op.drop_column("custom_model_catalog", "status_schedule")
