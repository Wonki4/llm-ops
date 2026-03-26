"""Add portal settings table for global configuration.

Revision ID: 007_portal_settings
Revises: 006_key_sequence
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "007_portal_settings"
down_revision = "006_key_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_portal_settings",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Seed defaults
    op.execute("INSERT INTO custom_portal_settings (key, value) VALUES ('default_tpm_limit', '100000')")
    op.execute("INSERT INTO custom_portal_settings (key, value) VALUES ('default_rpm_limit', '1000')")


def downgrade() -> None:
    op.drop_table("custom_portal_settings")
