"""Configurable readiness / liveness probes on recipes and deployments.

Adds ``probes`` (jsonb, nullable) to custom_serving_recipe and
custom_model_deployment: ``{"readiness": {...}, "liveness": {...}}`` with
every field optional. NULL keeps the behaviour that was hardcoded until now
(readiness on /health after 60 s, no liveness probe).

Revision ID: 048_serving_probes
Revises: 047_deployment_recipe_link
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "048_serving_probes"
down_revision = "047_deployment_recipe_link"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("probes", JSONB, nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "probes")
