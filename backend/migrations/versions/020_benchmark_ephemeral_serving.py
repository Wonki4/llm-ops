"""Ephemeral serving for benchmark runs (provision → bench → teardown).

Revision ID: 020_benchmark_ephemeral
Revises: 019_benchmark_serving
"""

import sqlalchemy as sa
from alembic import op

revision = "020_benchmark_ephemeral"
down_revision = "019_benchmark_serving"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "custom_benchmark_run",
        sa.Column("ephemeral", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column("serving_k8s_name", sa.String(256), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column("bench_image", sa.String(512), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column("serving_torn_down", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("custom_benchmark_run", "serving_torn_down")
    op.drop_column("custom_benchmark_run", "bench_image")
    op.drop_column("custom_benchmark_run", "serving_k8s_name")
    op.drop_column("custom_benchmark_run", "ephemeral")
