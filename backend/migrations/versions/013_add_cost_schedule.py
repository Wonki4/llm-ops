"""Add time-of-day cost schedules + default cost columns on model catalog.

Revision ID: 013_cost_schedule
Revises: 012_announcement_featured
"""

import sqlalchemy as sa
from alembic import op

revision = "013_cost_schedule"
down_revision = "012_announcement_featured"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fallback costs applied when no time-window rule matches.
    op.add_column(
        "custom_model_catalog",
        sa.Column("default_input_cost_per_token", sa.Float(), nullable=True),
    )
    op.add_column(
        "custom_model_catalog",
        sa.Column("default_output_cost_per_token", sa.Float(), nullable=True),
    )

    op.create_table(
        "custom_model_cost_schedule",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_name", sa.String(256), nullable=False, index=True),
        # ISO weekday integers stored as int array; 1=Mon..7=Sun.
        sa.Column(
            "days_of_week",
            sa.dialects.postgresql.ARRAY(sa.SmallInteger()),
            nullable=False,
        ),
        # UTC hour bounds [start, end); day-spanning when end <= start.
        sa.Column("hour_start_utc", sa.SmallInteger(), nullable=False),
        sa.Column("hour_end_utc", sa.SmallInteger(), nullable=False),
        sa.Column("input_cost_per_token", sa.Float(), nullable=False),
        sa.Column("output_cost_per_token", sa.Float(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "hour_start_utc >= 0 AND hour_start_utc <= 23",
            name="ck_cost_schedule_hour_start_range",
        ),
        sa.CheckConstraint(
            "hour_end_utc >= 1 AND hour_end_utc <= 24",
            name="ck_cost_schedule_hour_end_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("custom_model_cost_schedule")
    op.drop_column("custom_model_catalog", "default_output_cost_per_token")
    op.drop_column("custom_model_catalog", "default_input_cost_per_token")
