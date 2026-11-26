"""Optional per-cluster Prometheus query connection."""

import sqlalchemy as sa
from alembic import op

revision = "067_cluster_prometheus"
down_revision = "066_kv_observations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("custom_k8s_cluster", sa.Column("prometheus_url", sa.String(1024)))
    op.add_column(
        "custom_k8s_cluster", sa.Column("prometheus_shared", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("custom_k8s_cluster", sa.Column("prometheus_cluster_label", sa.String(128)))
    op.add_column("custom_k8s_cluster", sa.Column("prometheus_cluster_value", sa.String(256)))
    op.add_column("custom_k8s_cluster", sa.Column("prometheus_token_encrypted", sa.Text()))


def downgrade():
    for column in (
        "prometheus_token_encrypted",
        "prometheus_cluster_value",
        "prometheus_cluster_label",
        "prometheus_shared",
        "prometheus_url",
    ):
        op.drop_column("custom_k8s_cluster", column)
