"""llm-d: replace structured fields with a single helm_values blob

Drops the per-field columns (replicas, model_server_type, target_port,
endpoint_selector) and the values_override blob in favour of one authoritative
``helm_values`` JSONB the user edits directly as values.yaml. Existing stacks
carry their full applied values over from ``values_snapshot``.

Revision ID: 032_llmd_helm_values
Revises: 031_cost_schedule_local_tz
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "032_llmd_helm_values"
down_revision = "031_cost_schedule_local_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_llmd_stack",
        sa.Column("helm_values", JSONB, nullable=False, server_default="{}"),
    )
    # Existing stacks: the rendered snapshot is the full set of applied values.
    op.execute("UPDATE custom_llmd_stack SET helm_values = values_snapshot")
    op.drop_column("custom_llmd_stack", "values_override")
    op.drop_column("custom_llmd_stack", "replicas")
    op.drop_column("custom_llmd_stack", "model_server_type")
    op.drop_column("custom_llmd_stack", "target_port")
    op.drop_column("custom_llmd_stack", "endpoint_selector")


def downgrade() -> None:
    op.add_column("custom_llmd_stack", sa.Column("replicas", sa.Integer(), nullable=False, server_default="1"))
    op.add_column(
        "custom_llmd_stack",
        sa.Column("model_server_type", sa.String(64), nullable=False, server_default="vllm"),
    )
    op.add_column("custom_llmd_stack", sa.Column("target_port", sa.Integer(), nullable=False, server_default="8000"))
    op.add_column("custom_llmd_stack", sa.Column("endpoint_selector", sa.String(512), nullable=True))
    op.add_column(
        "custom_llmd_stack",
        sa.Column("values_override", JSONB, nullable=False, server_default="{}"),
    )
    op.execute("UPDATE custom_llmd_stack SET values_override = helm_values")
    op.drop_column("custom_llmd_stack", "helm_values")
