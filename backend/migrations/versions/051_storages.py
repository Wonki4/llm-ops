"""Storage objects + per-cluster/namespace bindings.

- custom_storage           (name unique, kind)
- custom_storage_binding   (storage × cluster × namespace → pvc | nfs)
- storages jsonb not null default '[]' on custom_serving_recipe / custom_model_deployment
- custom_k8s_cluster.bench_storage_id (FK SET NULL) + bench_mount_path

Data is moved by 052_storages_backfill.

Revision ID: 051_storages
Revises: 050_pd_serving
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "051_storages"
down_revision = "050_pd_serving"
branch_labels = None
depends_on = None

_TABLES = ("custom_serving_recipe", "custom_model_deployment")


def _audit() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "custom_storage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="weights"),
        *_audit(),
    )
    op.create_index("ix_custom_storage_name", "custom_storage", ["name"], unique=True)
    op.create_table(
        "custom_storage_binding",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "storage_id", UUID(as_uuid=True), sa.ForeignKey("custom_storage.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "cluster_id", UUID(as_uuid=True), sa.ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("namespace", sa.String(63), nullable=True),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("claim_name", sa.String(253), nullable=True),
        sa.Column("nfs_server", sa.String(255), nullable=True),
        sa.Column("nfs_path", sa.String(1024), nullable=True),
        sa.Column("sub_path", sa.String(1024), nullable=True),
        *_audit(),
        sa.UniqueConstraint(
            "storage_id", "cluster_id", "namespace", name="uq_storage_binding_scope", postgresql_nulls_not_distinct=True
        ),
        sa.CheckConstraint(
            "(type = 'pvc' AND claim_name IS NOT NULL AND namespace IS NOT NULL"
            " AND nfs_server IS NULL AND nfs_path IS NULL)"
            " OR (type = 'nfs' AND nfs_server IS NOT NULL AND nfs_path IS NOT NULL AND claim_name IS NULL)",
            name="ck_storage_binding_target",
        ),
    )
    op.create_index("ix_custom_storage_binding_storage_id", "custom_storage_binding", ["storage_id"])
    op.create_index("ix_custom_storage_binding_cluster_id", "custom_storage_binding", ["cluster_id"])
    for table in _TABLES:
        op.add_column(table, sa.Column("storages", JSONB, nullable=False, server_default="[]"))
    op.add_column(
        "custom_k8s_cluster",
        sa.Column(
            "bench_storage_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_storage.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("custom_k8s_cluster", sa.Column("bench_mount_path", sa.String(512), nullable=True))


def downgrade() -> None:
    op.drop_column("custom_k8s_cluster", "bench_mount_path")
    op.drop_column("custom_k8s_cluster", "bench_storage_id")
    for table in _TABLES:
        op.drop_column(table, "storages")
    op.drop_index("ix_custom_storage_binding_cluster_id", table_name="custom_storage_binding")
    op.drop_index("ix_custom_storage_binding_storage_id", table_name="custom_storage_binding")
    op.drop_table("custom_storage_binding")
    op.drop_index("ix_custom_storage_name", table_name="custom_storage")
    op.drop_table("custom_storage")
