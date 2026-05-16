"""Add deployment event log for status transitions and operator alerts.

Revision ID: 015_deployment_event
Revises: 014_model_deployment
"""

import sqlalchemy as sa
from alembic import op

revision = "015_deployment_event"
down_revision = "014_model_deployment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_model_deployment_event",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deployment_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_model_deployment.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),  # Created/Ready/Unhealthy/Failed/LitellmRegistered/etc
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),  # info/warning/error
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("alert_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("custom_model_deployment_event")
