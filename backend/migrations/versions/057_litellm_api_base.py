"""Optional LiteLLM API base override for a serving deployment."""

import sqlalchemy as sa
from alembic import op

revision = "057_litellm_api_base"
down_revision = "056_deployment_common_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_model_deployment", sa.Column("litellm_api_base", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "litellm_api_base")
