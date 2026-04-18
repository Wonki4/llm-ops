"""Add announcements table.

Revision ID: 010_announcements
Revises: 009_membership_expiry
"""

import sqlalchemy as sa
from alembic import op

revision = "010_announcements"
down_revision = "009_membership_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_announcements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_id", sa.String(128), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcements_published_created",
        "custom_announcements",
        ["is_published", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_announcements_published_created")
    op.drop_table("custom_announcements")
