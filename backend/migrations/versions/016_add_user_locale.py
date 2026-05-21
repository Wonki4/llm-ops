"""Add per-user locale preference (ko/en) on custom_users.

Revision ID: 016_user_locale
Revises: 015_deployment_event
"""

import sqlalchemy as sa
from alembic import op

revision = "016_user_locale"
down_revision = "015_deployment_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_users",
        sa.Column("locale", sa.String(8), nullable=False, server_default="ko"),
    )


def downgrade() -> None:
    op.drop_column("custom_users", "locale")
