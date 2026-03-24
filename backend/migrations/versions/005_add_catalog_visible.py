"""Add visible flag to model catalog.

Controls whether a model appears in the calendar and dashboard.

Revision ID: 005_catalog_visible
Revises: 004_request_type_budget
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa

revision = "005_catalog_visible"
down_revision = "004_request_type_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_model_catalog",
        sa.Column("visible", sa.Boolean, nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("custom_model_catalog", "visible")
