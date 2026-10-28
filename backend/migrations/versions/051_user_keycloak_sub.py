"""Pin portal users to their Keycloak account.

custom_users:
- keycloak_sub  varchar(64) null, indexed — the Keycloak `sub` first seen for
                this user id (사번). NULL until the next login records it; an
                admin resets it to NULL to hand the id to another account.

Revision ID: 051_user_keycloak_sub
Revises: 050_pd_serving
"""

import sqlalchemy as sa
from alembic import op

revision = "051_user_keycloak_sub"
down_revision = "050_pd_serving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("custom_users", sa.Column("keycloak_sub", sa.String(64), nullable=True))
    op.create_index("ix_custom_users_keycloak_sub", "custom_users", ["keycloak_sub"])


def downgrade() -> None:
    op.drop_index("ix_custom_users_keycloak_sub", table_name="custom_users")
    op.drop_column("custom_users", "keycloak_sub")
