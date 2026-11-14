"""Group serving recipes by their model repository."""

import sqlalchemy as sa
from alembic import op

revision = "060_recipe_hf_repo_id"
down_revision = "059_serving_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_serving_recipe", sa.Column("hf_repo_id", sa.String(256), nullable=True))
    op.execute("""
        UPDATE custom_serving_recipe SET hf_repo_id = model_path
        WHERE length(model_path) <= 256
          AND model_path ~ '^[A-Za-z0-9_][A-Za-z0-9._-]*/[A-Za-z0-9_][A-Za-z0-9._-]*$'
    """)


def downgrade() -> None:
    op.drop_column("custom_serving_recipe", "hf_repo_id")
