"""Requested period (days) on a budget-increase request.

NULL = permanent increase (today's behavior); a positive int = a temporary
increase applied as a member budget boost on approval.

Revision ID: 040_budget_request_duration
Revises: 039_llmd_stack_chart_source
"""

import sqlalchemy as sa
from alembic import op

revision = "040_budget_request_duration"
down_revision = "039_llmd_stack_chart_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_team_join_requests",
        sa.Column("requested_duration_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("custom_team_join_requests", "requested_duration_days")
