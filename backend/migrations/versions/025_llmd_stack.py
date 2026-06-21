"""llm-d serving stacks (ArgoCD-managed).

Revision ID: 025_llmd_stack
Revises: 024_cluster_default_nfs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "025_llmd_stack"
down_revision = "024_cluster_default_nfs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_llmd_stack",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("model_ref", sa.String(512), nullable=False),
        sa.Column("served_model_name", sa.String(256), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("namespace", sa.String(128), nullable=False, server_default="default"),
        sa.Column("argo_app_name", sa.String(253), nullable=False),
        sa.Column("replicas", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_resource_key", sa.String(64), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("values_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_custom_llmd_stack_name", "custom_llmd_stack", ["name"], unique=True)
    op.create_index("ix_custom_llmd_stack_cluster_id", "custom_llmd_stack", ["cluster_id"])
    op.create_foreign_key(
        "fk_custom_llmd_stack_cluster_id", "custom_llmd_stack", "custom_k8s_cluster",
        ["cluster_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_custom_llmd_stack_cluster_id", "custom_llmd_stack", type_="foreignkey")
    op.drop_index("ix_custom_llmd_stack_cluster_id", table_name="custom_llmd_stack")
    op.drop_index("ix_custom_llmd_stack_name", table_name="custom_llmd_stack")
    op.drop_table("custom_llmd_stack")
