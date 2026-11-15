"""Distinguish portal-managed defaults from prices discovered in LiteLLM."""

import sqlalchemy as sa
from alembic import op

revision = "062_managed_default_cost"
down_revision = "061_benchmark_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("default_cost_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("custom_model_catalog", "default_cost_managed")
