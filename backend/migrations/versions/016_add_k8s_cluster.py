"""Add multi-cluster registry and bind deployments to a cluster.

Revision ID: 016_k8s_cluster
Revises: 015_deployment_event

Adds a custom_k8s_cluster registry table and a cluster_id FK on
custom_model_deployment so each deployment can target a different
K8s cluster. On upgrade, if a row already exists in
custom_model_deployment we seed a "default" cluster pointing at the
APP_KUBECONFIG_PATH from env (best-effort; admin can rename/edit later)
and backfill existing deployments to it.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "016_k8s_cluster"
down_revision = "015_deployment_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_k8s_cluster",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("kubeconfig_content", sa.Text(), nullable=False),
        sa.Column("default_namespace", sa.String(128), nullable=False, server_default="default"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.add_column(
        "custom_model_deployment",
        sa.Column("cluster_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_deployment_cluster",
        "custom_model_deployment",
        "custom_k8s_cluster",
        ["cluster_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_deployment_cluster_id",
        "custom_model_deployment",
        ["cluster_id"],
    )

    # Backfill: if there are existing deployments, seed a "default" cluster from
    # the legacy APP_KUBECONFIG_PATH (read the file content if present) and
    # point all deployments at it. Without that env var we can't fill
    # kubeconfig_content sensibly — admin must visit the new cluster page and
    # edit before the reconciler will work.
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT COUNT(*) FROM custom_model_deployment")).scalar() or 0
    if existing > 0:
        path = os.environ.get("APP_KUBECONFIG_PATH", "")
        kubeconfig = ""
        if path and os.path.isfile(path):
            try:
                with open(path) as f:
                    kubeconfig = f.read()
            except OSError:
                kubeconfig = ""
        bind.execute(
            sa.text(
                "INSERT INTO custom_k8s_cluster "
                "(id, name, kubeconfig_content, default_namespace, description, enabled, created_by, updated_by) "
                "VALUES (gen_random_uuid(), 'default', :kc, 'default', :desc, true, 'system', 'system')"
            ),
            {
                "kc": kubeconfig,
                "desc": "Seeded from APP_KUBECONFIG_PATH during migration."
                if kubeconfig
                else "Placeholder — paste kubeconfig before the reconciler can reach this cluster.",
            },
        )
        bind.execute(
            sa.text(
                "UPDATE custom_model_deployment SET cluster_id = "
                "(SELECT id FROM custom_k8s_cluster WHERE name = 'default') "
                "WHERE cluster_id IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_index("ix_deployment_cluster_id", table_name="custom_model_deployment")
    op.drop_constraint("fk_deployment_cluster", "custom_model_deployment", type_="foreignkey")
    op.drop_column("custom_model_deployment", "cluster_id")
    op.drop_table("custom_k8s_cluster")
