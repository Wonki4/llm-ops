"""Rename cost-schedule hour columns from UTC to local-timezone semantics.

Time-of-day cost rules are now authored and stored in the configured schedule
timezone (settings.schedule_timezone, default Asia/Seoul) instead of UTC. The
worker converts `now` into that zone before matching, so the stored hours are
wall-clock local hours. Rename the columns to match; existing values are
re-interpreted as local (admins re-register rules after this change).

Revision ID: 031_cost_schedule_local_tz
Revises: 030_llmd_chart_options
"""

from alembic import op

revision = "031_cost_schedule_local_tz"
down_revision = "030_llmd_chart_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("custom_model_cost_schedule", "hour_start_utc", new_column_name="hour_start_local")
    op.alter_column("custom_model_cost_schedule", "hour_end_utc", new_column_name="hour_end_local")


def downgrade() -> None:
    op.alter_column("custom_model_cost_schedule", "hour_start_local", new_column_name="hour_start_utc")
    op.alter_column("custom_model_cost_schedule", "hour_end_local", new_column_name="hour_end_utc")
