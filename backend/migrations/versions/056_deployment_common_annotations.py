"""Common pod annotations on deployments.

custom_model_deployment:
- common_annotations  jsonb null — the portal's common pod annotation rules
                      ({key: value template}, portal setting `serving_pod_annotations`)
                      the deployment was last created / re-applied with. NULL for
                      existing rows: they get the annotations on their next re-apply.

Revision ID: 056_deployment_common_annotations
Revises: 055_deployment_common_labels
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "056_deployment_common_annotations"
down_revision = "055_deployment_common_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_model_deployment", sa.Column("common_annotations", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "common_annotations")
