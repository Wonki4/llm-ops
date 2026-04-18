"""Add is_featured column to announcements (at most one).

Revision ID: 012_announcement_featured
Revises: 011_announcement_pin
"""

import sqlalchemy as sa
from alembic import op

revision = "012_announcement_featured"
down_revision = "011_announcement_pin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_announcements",
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Partial unique index: at most one row with is_featured = true
    op.create_index(
        "ux_announcements_single_featured",
        "custom_announcements",
        ["is_featured"],
        unique=True,
        postgresql_where=sa.text("is_featured = true"),
    )


def downgrade() -> None:
    op.drop_index("ux_announcements_single_featured")
    op.drop_column("custom_announcements", "is_featured")
