"""Admin-registered Kubernetes clusters (multi-cluster portal settings).

Revision ID: 021_k8s_clusters
Revises: 020_benchmark_ephemeral
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "021_k8s_clusters"
down_revision = "020_benchmark_ephemeral"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_k8s_cluster",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("context", sa.String(256), nullable=False),
        sa.Column("namespace", sa.String(128), nullable=False, server_default="default"),
        sa.Column("kubeconfig_encrypted", sa.Text(), nullable=False),
        sa.Column("api_server", sa.String(512), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_custom_k8s_cluster_name", "custom_k8s_cluster", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_custom_k8s_cluster_name", table_name="custom_k8s_cluster")
    op.drop_table("custom_k8s_cluster")
