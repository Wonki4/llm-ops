"""Retarget llm-d stacks at an existing model (EPP router, standalone chart).

The gateway-api-inference-extension standalone chart routes to already-running
model servers, so a stack references an existing model rather than provisioning
one: rename model_ref -> target_model_name and drop the served-name / GPU columns.

Revision ID: 029_llmd_target_model
Revises: 028_llmd_argocd_connection
"""

import sqlalchemy as sa
from alembic import op

revision = "029_llmd_target_model"
down_revision = "028_llmd_argocd_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("custom_llmd_stack", "model_ref", new_column_name="target_model_name")
    op.drop_column("custom_llmd_stack", "served_model_name")
    op.drop_column("custom_llmd_stack", "gpu_count")
    op.drop_column("custom_llmd_stack", "gpu_resource_key")


def downgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("gpu_resource_key", sa.String(64), nullable=False, server_default="nvidia.com/gpu"),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("served_model_name", sa.String(256), nullable=False, server_default=""),
    )
    op.alter_column("custom_llmd_stack", "target_model_name", new_column_name="model_ref")
