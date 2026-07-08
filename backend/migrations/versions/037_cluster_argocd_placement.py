"""Per-cluster ArgoCD placement for llm-d stacks.

argocd_host_cluster_id: the cluster whose ArgoCD manages this one (NULL =
itself); the Application CR is applied there. argocd_dest_server: the
Application's spec.destination.server (NULL = https://kubernetes.default.svc).

Revision ID: 037_cluster_argocd_placement
Revises: 036_drop_argocd_connection
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "037_cluster_argocd_placement"
down_revision = "036_drop_argocd_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_host_cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("argocd_dest_server", sa.String(512), nullable=True),
    )
    op.create_foreign_key(
        "fk_k8s_cluster_argocd_host",
        "custom_k8s_cluster",
        "custom_k8s_cluster",
        ["argocd_host_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_k8s_cluster_argocd_host", "custom_k8s_cluster", type_="foreignkey")
    op.drop_column("custom_k8s_cluster", "argocd_dest_server")
    op.drop_column("custom_k8s_cluster", "argocd_host_cluster_id")
