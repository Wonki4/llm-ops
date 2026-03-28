"""Add redis catalog sync table for PostgreSQL backup.

Revision ID: 008_redis_catalog_sync
Revises: 007_portal_settings
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "008_redis_catalog_sync"
down_revision = "007_portal_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_redis_catalog",
        sa.Column("display_name", sa.String(512), primary_key=True),
        sa.Column("data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("custom_redis_catalog")
