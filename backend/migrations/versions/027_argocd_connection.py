"""Registered ArgoCD connections (server URL + encrypted API token).

Revision ID: 027_argocd_connection
Revises: 026_llmd_stack
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "027_argocd_connection"
down_revision = "026_llmd_stack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_argocd_connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("server_url", sa.String(512), nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("insecure_skip_verify", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_custom_argocd_connection_name", "custom_argocd_connection", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_custom_argocd_connection_name", table_name="custom_argocd_connection")
    op.drop_table("custom_argocd_connection")
