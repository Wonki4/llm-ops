"""Add request_type and requested_budget to team join requests.

Supports budget increase requests alongside join requests.

Revision ID: 004_request_type_budget
Revises: 003_status_schedule
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "004_request_type_budget"
down_revision = "003_status_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_team_join_requests",
        sa.Column("request_type", sa.String(32), nullable=False, server_default="join"),
    )
    op.add_column(
        "custom_team_join_requests",
        sa.Column("requested_budget", sa.Float, nullable=True),
    )
    op.create_index("ix_custom_team_join_requests_request_type", "custom_team_join_requests", ["request_type"])


def downgrade() -> None:
    op.drop_index("ix_custom_team_join_requests_request_type", table_name="custom_team_join_requests")
    op.drop_column("custom_team_join_requests", "requested_budget")
    op.drop_column("custom_team_join_requests", "request_type")
