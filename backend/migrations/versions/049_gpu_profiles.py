"""GPU type profiles.

- custom_k8s_cluster.gpu_label_key: the node label key this cluster uses to
  mark GPU kinds (hand-managed, no GPU Feature Discovery). Default 'gpu-type'.
- custom_gpu_profile: per-cluster named placement bundle for one GPU kind
  (label key override, label value, resource key, tolerations, display meta).
  cluster_id NULL = the portal default cluster, as elsewhere.
- custom_serving_recipe.gpu_type / custom_model_deployment.gpu_type: the
  profile *name* a recipe asks for and the one a deployment was resolved with.

Revision ID: 049_gpu_profiles
Revises: 048_serving_probes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "049_gpu_profiles"
down_revision = "048_serving_probes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_k8s_cluster",
        sa.Column("gpu_label_key", sa.String(128), nullable=False, server_default="gpu-type"),
    )
    op.create_table(
        "custom_gpu_profile",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cluster_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("label_key", sa.String(128), nullable=True),
        sa.Column("label_value", sa.String(128), nullable=False),
        sa.Column("gpu_resource_key", sa.String(128), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("tolerations", JSONB, nullable=True),
        sa.Column("vram_gb", sa.Integer, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "cluster_id", "name", name="uq_gpu_profile_cluster_name", postgresql_nulls_not_distinct=True
        ),
    )
    op.create_index("ix_custom_gpu_profile_cluster_id", "custom_gpu_profile", ["cluster_id"])
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        op.add_column(table, sa.Column("gpu_type", sa.String(64), nullable=True))


def downgrade() -> None:
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        op.drop_column(table, "gpu_type")
    op.drop_index("ix_custom_gpu_profile_cluster_id", table_name="custom_gpu_profile")
    op.drop_table("custom_gpu_profile")
    op.drop_column("custom_k8s_cluster", "gpu_label_key")
