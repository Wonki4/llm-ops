"""Benchmark sweeps: sweep table + sweep linkage columns on runs.

A sweep expands 1-2 serve-flag variables into sequential self-serving
benchmark Jobs; runs carry their prebuilt manifest while status=queued.

Revision ID: 041_benchmark_sweeps
Revises: 040_budget_request_duration
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "041_benchmark_sweeps"
down_revision = "040_budget_request_duration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_benchmark_sweep",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("deployment_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("external_source", JSONB(), nullable=True),
        sa.Column(
            "cluster_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("k8s_namespace", sa.String(128), nullable=False),
        sa.Column("preset", sa.String(32), nullable=False),
        sa.Column("variables", JSONB(), nullable=False),
        sa.Column("serving_overrides", JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running", index=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "custom_benchmark_run",
        sa.Column(
            "sweep_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_benchmark_sweep.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run", ["sweep_id"])
    op.add_column("custom_benchmark_run", sa.Column("sweep_index", sa.Integer(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("sweep_combo", JSONB(), nullable=True))
    op.add_column("custom_benchmark_run", sa.Column("queued_job_manifest", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_benchmark_run", "queued_job_manifest")
    op.drop_column("custom_benchmark_run", "sweep_combo")
    op.drop_column("custom_benchmark_run", "sweep_index")
    op.drop_index("ix_custom_benchmark_run_sweep_id", "custom_benchmark_run")
    op.drop_column("custom_benchmark_run", "sweep_id")
    op.drop_table("custom_benchmark_sweep")
