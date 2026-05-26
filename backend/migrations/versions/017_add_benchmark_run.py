"""Add benchmark run tracking for vllm/sglang/lm-eval against LiteLLM models.

Revision ID: 017_benchmark_run
Revises: 016_user_locale
"""

import sqlalchemy as sa
from alembic import op

revision = "017_benchmark_run"
down_revision = "016_user_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_benchmark_run",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_name", sa.String(256), nullable=False, index=True),
        sa.Column("tool", sa.String(32), nullable=False),  # vllm_serving / sglang_serving / lm_eval
        sa.Column("kind", sa.String(16), nullable=False),  # performance / accuracy
        sa.Column("params", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
            index=True,
        ),  # pending / running / succeeded / failed / cancelled
        sa.Column("k8s_job_name", sa.String(256), nullable=True),
        sa.Column("k8s_namespace", sa.String(128), nullable=True),
        sa.Column("result", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("custom_benchmark_run")
