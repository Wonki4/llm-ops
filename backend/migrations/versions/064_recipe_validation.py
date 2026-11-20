"""Durable recipe validation, immutable candidates and retained attempts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "064_recipe_validation"
down_revision = "063_applied_cost_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_recipe_validation",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False, index=True),
        sa.Column("cluster_id", UUID(), sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT")),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("conditions", JSONB(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("selected_candidate_id", UUID()),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("created_by", "request_key"),
    )
    op.create_table(
        "custom_validation_candidate",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column(
            "validation_id",
            UUID(),
            sa.ForeignKey("custom_recipe_validation.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("recipe_id", UUID(), sa.ForeignKey("custom_serving_recipe.id", ondelete="SET NULL")),
        sa.Column("parent_candidate_id", UUID()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("weights", sa.String(512), nullable=False),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_encrypted", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assessment", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("validation_id", "position"),
        sa.UniqueConstraint("validation_id", "request_key"),
    )
    op.create_table(
        "custom_validation_attempt",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column(
            "validation_id",
            UUID(),
            sa.ForeignKey("custom_recipe_validation.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "candidate_id",
            UUID(),
            sa.ForeignKey("custom_validation_candidate.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("deployment_id", UUID()),
        sa.Column("state", JSONB(), nullable=False),
        sa.Column("diagnostics", JSONB()),
        sa.Column("analysis", JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("stage_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("candidate_id", "number"),
    )
    for table in ("custom_model_deployment", "custom_benchmark_run"):
        op.add_column(
            table, sa.Column("validation_id", UUID(), sa.ForeignKey("custom_recipe_validation.id", ondelete="RESTRICT"))
        )
        op.add_column(
            table,
            sa.Column(
                "validation_attempt_id", UUID(), sa.ForeignKey("custom_validation_attempt.id", ondelete="RESTRICT")
            ),
        )
        op.create_index(f"ix_{table}_validation_id", table, ["validation_id"])
    op.create_index("ix_custom_benchmark_run_validation_attempt_id", "custom_benchmark_run", ["validation_attempt_id"])
    op.add_column("custom_model_deployment", sa.Column("validation_state", sa.String(16)))
    op.add_column(
        "custom_llmd_stack", sa.Column("validation_private", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    op.drop_column("custom_llmd_stack", "validation_private")
    op.drop_column("custom_model_deployment", "validation_state")
    for table in ("custom_model_deployment", "custom_benchmark_run"):
        op.drop_column(table, "validation_attempt_id")
        op.drop_column(table, "validation_id")
    for table in ("custom_validation_attempt", "custom_validation_candidate", "custom_recipe_validation"):
        op.drop_table(table)
