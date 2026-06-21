"""Configurable standalone-chart options for an llm-d stack.

Adds the model-server selector / type / port and a free-form values override
(deep-merged into the generated chart values), so operators can set any chart
option.

Revision ID: 030_llmd_chart_options
Revises: 029_llmd_target_model
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "030_llmd_chart_options"
down_revision = "029_llmd_target_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("model_server_type", sa.String(32), nullable=False, server_default="vllm"),
    )
    op.add_column(
        "custom_llmd_stack",
        sa.Column("target_port", sa.Integer(), nullable=False, server_default="8000"),
    )
    op.add_column("custom_llmd_stack", sa.Column("endpoint_selector", sa.String(512), nullable=True))
    op.add_column(
        "custom_llmd_stack",
        sa.Column("values_override", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("custom_llmd_stack", "values_override")
    op.drop_column("custom_llmd_stack", "endpoint_selector")
    op.drop_column("custom_llmd_stack", "target_port")
    op.drop_column("custom_llmd_stack", "model_server_type")
