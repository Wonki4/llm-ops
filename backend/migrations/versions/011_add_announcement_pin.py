"""Add is_pinned column to announcements.

Revision ID: 011_announcement_pin
Revises: 010_announcements
"""

import sqlalchemy as sa
from alembic import op

revision = "011_announcement_pin"
down_revision = "010_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_announcements",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_announcements_pinned_created",
        "custom_announcements",
        ["is_pinned", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcements_pinned_created")
    op.drop_column("custom_announcements", "is_pinned")
