"""External MCP recipe search completion state; limits live in immutable conditions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "065_recipe_search"
down_revision = "064_recipe_validation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_recipe_validation", sa.Column("search_state", JSONB()))


def downgrade():
    op.drop_column("custom_recipe_validation", "search_state")
