"""Temporary team-member budget boosts.

One row per boost: snapshot of the member's budget at boost time, the boosted
value, the expiry, and a status the worker moves active -> reverted (or an
admin moves active -> cancelled). At most one active boost per (team, user).

Revision ID: 038_member_budget_boost
Revises: 037_cluster_argocd_placement
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "038_member_budget_boost"
down_revision = "037_cluster_argocd_placement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_member_budget_boost",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("original_max_budget", sa.Float(), nullable=False),
        sa.Column("boost_max_budget", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_member_boost_team_user",
        "custom_member_budget_boost",
        ["team_id", "user_id"],
    )
    # One active boost per (team, user).
    op.create_index(
        "uq_member_boost_one_active",
        "custom_member_budget_boost",
        ["team_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_member_boost_one_active", table_name="custom_member_budget_boost")
    op.drop_index("ix_member_boost_team_user", table_name="custom_member_budget_boost")
    op.drop_table("custom_member_budget_boost")
