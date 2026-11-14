"""Team roles scoped to a serving cluster and namespace."""

import sqlalchemy as sa
from alembic import op

revision = "059_serving_grants"
down_revision = "058_aggregated_router"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_serving_grant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), sa.ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE")),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.UniqueConstraint("team_id", "cluster_id", "namespace", name="uq_serving_grant_scope",
                            postgresql_nulls_not_distinct=True),
        sa.CheckConstraint("role IN ('viewer', 'operator')", name="ck_serving_grant_role"),
    )
    op.create_table(
        "custom_serving_grant_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("cluster_id", sa.Uuid()),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("role", sa.String(16)),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("custom_serving_grant_event")
    op.drop_table("custom_serving_grant")
