"""Link benchmark runs to a portal-managed serving deployment + snapshot its config.

Revision ID: 019_benchmark_serving
Revises: 018_trusted_system
"""

import sqlalchemy as sa
from alembic import op

revision = "019_benchmark_serving"
down_revision = "018_trusted_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_benchmark_run",
        sa.Column("deployment_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column("serving_snapshot", sa.dialects.postgresql.JSONB, nullable=True),
    )
    op.create_index(
        "ix_custom_benchmark_run_deployment_id",
        "custom_benchmark_run",
        ["deployment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_benchmark_run_deployment_id", table_name="custom_benchmark_run")
    op.drop_column("custom_benchmark_run", "serving_snapshot")
    op.drop_column("custom_benchmark_run", "deployment_id")
