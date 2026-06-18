"""Add icon_url column to custom_model_catalog.

Revision ID: 018_model_icon_url
Revises: 017_benchmark_run
"""

import sqlalchemy as sa
from alembic import op

revision = "018_model_icon_url"
down_revision = "017_benchmark_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("icon_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_model_catalog", "icon_url")
