"""Cluster-level default PVC for benchmarks.

Adds an optional default model-weights PVC (claim name + mount path) to a
registered cluster. Benchmarks against a raw model_name fall back to this when no
per-run override is given. Both nullable, non-secret.

Revision ID: 023_cluster_default_pvc
Revises: 022_bench_deploy_cluster
"""

import sqlalchemy as sa
from alembic import op

revision = "023_cluster_default_pvc"
down_revision = "022_bench_deploy_cluster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("default_pvc_name", sa.String(253), nullable=True),
    )
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("default_pvc_mount_path", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "default_pvc_mount_path")
    op.drop_column("custom_k8s_cluster", "default_pvc_name")
