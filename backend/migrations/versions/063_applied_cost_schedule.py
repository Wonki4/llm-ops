"""Track successful price writes separately for each LiteLLM deployment."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "063_applied_cost_schedule"
down_revision = "062_managed_default_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing prices have no proof of who set them. Begin tracking with the next write.
    op.create_table(
        "custom_applied_cost_schedule",
        sa.Column("deployment_id", sa.String(), primary_key=True),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("litellm_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_rates", JSONB(), nullable=False),
        sa.Column("applied_rates", JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("custom_applied_cost_schedule")
