"""Short-lived, shared Prometheus query results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "068_model_latency_cache"
down_revision = "067_cluster_prometheus"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "custom_latency_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner", postgresql.UUID(as_uuid=True)),
        sa.Column("payload", postgresql.JSONB()),
    )
    op.create_index("ix_custom_latency_cache_expires_at", "custom_latency_cache", ["expires_at"])


def downgrade():
    op.drop_table("custom_latency_cache")
