"""Add prompt-caching read cost (cache_read) to catalog + cost schedule

Adds two nullable columns mirroring the existing input/output token costs:
``custom_model_catalog.default_cache_read_cost_per_token`` (snapshot default) and
``custom_model_cost_schedule.cache_read_cost_per_token`` (per-rule override).
Both nullable — existing rows predate prompt-caching pricing.

Revision ID: 033_cache_read_cost
Revises: 032_llmd_helm_values
"""

from alembic import op
import sqlalchemy as sa

revision = "033_cache_read_cost"
down_revision = "032_llmd_helm_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("default_cache_read_cost_per_token", sa.Float(), nullable=True),
    )
    op.add_column(
        "custom_model_cost_schedule",
        sa.Column("cache_read_cost_per_token", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_model_cost_schedule", "cache_read_cost_per_token")
    op.drop_column("custom_model_catalog", "default_cache_read_cost_per_token")
