"""Initial custom tables - users, team join requests, model catalog.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types via raw SQL
    op.execute("CREATE TYPE custom_global_role AS ENUM ('user', 'super_user')")
    op.execute("CREATE TYPE custom_join_request_status AS ENUM ('pending', 'approved', 'rejected')")
    op.execute("CREATE TYPE custom_model_status AS ENUM ('testing', 'prerelease', 'lts', 'deprecating', 'deprecated')")

    # Use postgresql.ENUM with create_type=False to reference existing enums without recreating
    custom_global_role = postgresql.ENUM("user", "super_user", name="custom_global_role", create_type=False)
    custom_join_request_status = postgresql.ENUM(
        "pending", "approved", "rejected", name="custom_join_request_status", create_type=False
    )
    custom_model_status = postgresql.ENUM(
        "testing",
        "prerelease",
        "lts",
        "deprecating",
        "deprecated",
        name="custom_model_status",
        create_type=False,
    )

    # --- custom_users ---
    op.create_table(
        "custom_users",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column(
            "global_role",
            custom_global_role,
            nullable=False,
            server_default="user",
        ),
        sa.Column("litellm_user_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- custom_team_join_requests ---
    op.create_table(
        "custom_team_join_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("requester_id", sa.String(128), nullable=False, index=True),
        sa.Column("team_id", sa.String(256), nullable=False, index=True),
        sa.Column("team_alias", sa.String(256), nullable=True),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column(
            "status",
            custom_join_request_status,
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("review_comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- custom_model_catalog ---
    op.create_table(
        "custom_model_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_name", sa.String(256), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("cost_info", postgresql.JSON, nullable=True),
        sa.Column(
            "status",
            custom_model_status,
            nullable=False,
            server_default="testing",
            index=True,
        ),
        sa.Column("status_change_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_deprecate_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("input_cost_per_token", sa.Float, nullable=True),
        sa.Column("output_cost_per_token", sa.Float, nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("custom_model_catalog")
    op.drop_table("custom_team_join_requests")
    op.drop_table("custom_users")

    op.execute("DROP TYPE IF EXISTS custom_model_status")
    op.execute("DROP TYPE IF EXISTS custom_join_request_status")
    op.execute("DROP TYPE IF EXISTS custom_global_role")
