"""custom_model_deployment.litellm_model_name — the name the deployment is registered under in LiteLLM.

Null = model_name (the K8s name), so existing rows keep their registration.
Not unique: deployments sharing a name are load-balanced by LiteLLM.

Revision ID: 053_litellm_model_name
Revises: 052_storages_backfill
"""

import sqlalchemy as sa
from alembic import op

revision = "053_litellm_model_name"
down_revision = "052_storages_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_model_deployment", sa.Column("litellm_model_name", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "litellm_model_name")
