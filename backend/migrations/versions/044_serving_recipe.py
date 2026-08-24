"""Create custom_serving_recipe (reusable vLLM serving templates).

Revision ID: 044_serving_recipe
Revises: 043_llmd_stack_ingress_overrides
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "044_serving_recipe"
down_revision = "043_llmd_stack_ingress_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_serving_recipe",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model_path", sa.String(512), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gpu_resource_key", sa.String(128), nullable=False, server_default="nvidia.com/gpu"),
        sa.Column("cpu_request", sa.String(32), nullable=True),
        sa.Column("cpu_limit", sa.String(32), nullable=True),
        sa.Column("memory_request", sa.String(32), nullable=True),
        sa.Column("memory_limit", sa.String(32), nullable=True),
        sa.Column("node_selector", JSONB(), nullable=True),
        sa.Column("tolerations", JSONB(), nullable=True),
        sa.Column("pvc_name", sa.String(256), nullable=True),
        sa.Column("pvc_mount_path", sa.String(512), nullable=True),
        sa.Column("vllm_extra_args", JSONB(), nullable=True),
        sa.Column("env", JSONB(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        op.f("ix_custom_serving_recipe_name"), "custom_serving_recipe", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_custom_serving_recipe_name"), table_name="custom_serving_recipe")
    op.drop_table("custom_serving_recipe")
