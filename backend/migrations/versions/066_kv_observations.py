"""Preserve KV capacity for each observed Pod restart."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "066_kv_observations"
down_revision = "065_recipe_search"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_kv_observation",
        sa.Column(
            "deployment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_model_deployment.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), primary_key=True),
        sa.Column("pod_uid", sa.String(128), primary_key=True),
        sa.Column("restart", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", UUID(as_uuid=True), sa.ForeignKey("custom_validation_attempt.id", ondelete="CASCADE")),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("pod_name", sa.String(253), nullable=False),
        sa.Column("engine", sa.String(16), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("image_id", sa.String(768)),
        sa.Column("gpu_type", sa.String(64)),
        sa.Column("revision", sa.String(256)),
        sa.Column("capacity_tokens", sa.BigInteger()),
        sa.Column("budget_bytes", sa.BigInteger()),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
    )
    op.create_index("ix_kv_observation_fingerprint", "custom_kv_observation", ["fingerprint", "last_seen"])
    op.create_index("ix_kv_observation_attempt", "custom_kv_observation", ["attempt_id"])


def downgrade():
    op.drop_table("custom_kv_observation")
