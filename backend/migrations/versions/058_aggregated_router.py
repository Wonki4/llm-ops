"""Optional dedicated router settings for aggregated serving."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "058_aggregated_router"
down_revision = "057_litellm_api_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("custom_serving_recipe", "custom_model_deployment"):
        op.add_column(table, sa.Column("aggregated_router", JSONB(none_as_null=True), nullable=True))
    op.add_column("custom_model_deployment", sa.Column("router_apply_error", sa.Text(), nullable=True))
    op.add_column("custom_llmd_stack", sa.Column("ingress_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_llmd_stack", "ingress_path")
    op.drop_column("custom_model_deployment", "router_apply_error")
    for table in ("custom_model_deployment", "custom_serving_recipe"):
        op.drop_column(table, "aggregated_router")
