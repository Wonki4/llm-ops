"""Common pod annotations on deployments.

custom_model_deployment:
- common_annotations  jsonb null — the portal's common pod annotation rules
                      ({key: value template}, portal setting `serving_pod_annotations`)
                      the deployment was last created / re-applied with. NULL for
                      existing rows: they get the annotations on their next re-apply.

custom_alembic_version:
- version_num  varchar(32) → varchar(255). Alembic creates the version table with
               32 characters and this revision id is 33; widened before alembic
               writes it (same transaction). Kept wide on downgrade.

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
    op.alter_column(
        "custom_alembic_version",
        "version_num",
        existing_type=sa.String(32),
        type_=sa.String(255),
        existing_nullable=False,
    )
    op.add_column("custom_model_deployment", sa.Column("common_annotations", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("custom_model_deployment", "common_annotations")
