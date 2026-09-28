"""Deployment → recipe back-reference.

custom_model_deployment.recipe_id (uuid, nullable, FK custom_serving_recipe
ON DELETE SET NULL): the recipe a deployment was launched from, or the recipe
that was captured from it. Purely informational — manifests are rendered from
the deployment's own columns, so the link never affects what runs.

Revision ID: 047_deployment_recipe_link
Revises: 046_serving_engine
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "047_deployment_recipe_link"
down_revision = "046_serving_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_deployment",
        sa.Column(
            "recipe_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_serving_recipe.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_custom_model_deployment_recipe_id", "custom_model_deployment", ["recipe_id"])


def downgrade() -> None:
    op.drop_index("ix_custom_model_deployment_recipe_id", table_name="custom_model_deployment")
    op.drop_column("custom_model_deployment", "recipe_id")
