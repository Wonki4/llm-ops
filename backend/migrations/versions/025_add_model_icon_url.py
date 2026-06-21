"""Add icon_url column to custom_model_catalog.

Revision ID: 025_model_icon_url
Revises: 024_cluster_default_nfs
"""

import sqlalchemy as sa
from alembic import op

revision = "025_model_icon_url"
down_revision = "024_cluster_default_nfs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("icon_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_model_catalog", "icon_url")
