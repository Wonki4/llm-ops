"""Target benchmarks & deployments at a registered K8s cluster.

Adds a nullable cluster_id FK (ON DELETE RESTRICT) to custom_benchmark_run and
custom_model_deployment. Null = portal default (mounted kubeconfig).

Revision ID: 022_bench_deploy_cluster
Revises: 021_k8s_clusters
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022_bench_deploy_cluster"
down_revision = "021_k8s_clusters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("custom_benchmark_run", "custom_model_deployment"):
        op.add_column(
            table,
            sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(f"ix_{table}_cluster_id", table, ["cluster_id"])
        op.create_foreign_key(
            f"fk_{table}_cluster_id",
            table,
            "custom_k8s_cluster",
            ["cluster_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for table in ("custom_benchmark_run", "custom_model_deployment"):
        op.drop_constraint(f"fk_{table}_cluster_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_cluster_id", table_name=table)
        op.drop_column(table, "cluster_id")
