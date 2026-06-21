"""Switch the cluster-level benchmark mount from PVC to NFS.

Replaces the default_pvc_* columns (added in 023) with an inline NFS mount:
server + export path + container mount path. Benchmarks against a raw model_name
attach this NFS volume directly (no pre-created PVC). All nullable, non-secret.

Revision ID: 024_cluster_default_nfs
Revises: 023_cluster_default_pvc
"""

import sqlalchemy as sa
from alembic import op

revision = "024_cluster_default_nfs"
down_revision = "023_cluster_default_pvc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("custom_k8s_cluster", "default_pvc_name")
    op.drop_column("custom_k8s_cluster", "default_pvc_mount_path")
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_server", sa.String(253), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_path", sa.String(512), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_nfs_mount_path", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "default_nfs_mount_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_path")
    op.drop_column("custom_k8s_cluster", "default_nfs_server")
    op.add_column("custom_k8s_cluster", sa.Column("default_pvc_name", sa.String(253), nullable=True))
    op.add_column("custom_k8s_cluster", sa.Column("default_pvc_mount_path", sa.String(512), nullable=True))
