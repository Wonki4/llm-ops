"""Common pod labels on deployments.

custom_model_deployment:
- common_labels  jsonb null — the portal's common pod label rules
                 ({key: value template}, portal setting `serving_pod_labels`)
                 the deployment was last created / re-applied with. NULL for
                 existing rows: they get the labels on their next re-apply.

Revision ID: 055_deployment_common_labels
Revises: 054_user_keycloak_sub
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "055_deployment_common_labels"
down_revision = "054_user_keycloak_sub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_model_deployment", sa.Column("common_labels", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "common_labels")
