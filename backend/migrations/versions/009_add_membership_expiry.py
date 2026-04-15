"""Add team membership expiry table.

Revision ID: 009_membership_expiry
Revises: 008_redis_catalog_sync
"""

from alembic import op
import sqlalchemy as sa

revision = "009_membership_expiry"
down_revision = "008_redis_catalog_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_team_membership",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_membership_expiry_status_expires",
        "custom_team_membership",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_membership_expiry_user_team",
        "custom_team_membership",
        ["user_id", "team_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_membership_expiry_user_team")
    op.drop_index("ix_membership_expiry_status_expires")
    op.drop_table("custom_team_membership")
