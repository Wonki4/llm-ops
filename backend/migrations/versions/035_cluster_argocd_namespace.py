"""Per-cluster ArgoCD control-plane namespace.

Adds custom_k8s_cluster.argocd_namespace (default 'argocd'); llm-d stacks
apply their Application CR into this namespace via the K8s API.

Revision ID: 035_cluster_argocd_namespace
Revises: 034_external_serving
"""

import sqlalchemy as sa
from alembic import op

revision = "035_cluster_argocd_namespace"
down_revision = "034_external_serving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_namespace", sa.String(128), nullable=False, server_default="argocd"),
    )


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "argocd_namespace")
