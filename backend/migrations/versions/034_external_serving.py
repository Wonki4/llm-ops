"""External serving registrations (LiteLLM mapping for discovered vLLM/SGLang)

Stores only registration state; discovery is a live scan. Unique on
(cluster_id, namespace, deployment_name) with NULLS NOT DISTINCT so
default-cluster (NULL) rows can't duplicate either.

Revision ID: 034_external_serving
Revises: 033_cache_read_cost
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "034_external_serving"
down_revision = "033_cache_read_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_external_serving",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("deployment_name", sa.String(253), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=False),
        sa.Column("litellm_model_id", sa.String(128), nullable=False),
        sa.Column("registered_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "cluster_id", "namespace", "deployment_name",
            name="uq_external_serving_target",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("custom_external_serving")
