"""Storage: a named piece of data (weights, cache, datasets) workloads mount.

A storage is cluster-agnostic. ``CustomStorageBinding`` says what it physically
is on one cluster × namespace: an existing PVC, or an NFS export. Recipes and
deployments list storages (``storages`` JSONB); the concrete volume is resolved
from the deploy target at deploy time (see services/storage_store).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomStorage(CustomBase):
    __tablename__ = "custom_storage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # weights | cache | data | other — display/filter only.
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="weights", server_default="weights")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CustomStorageBinding(CustomBase):
    __tablename__ = "custom_storage_binding"
    __table_args__ = (
        UniqueConstraint(
            "storage_id", "cluster_id", "namespace", name="uq_storage_binding_scope", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(
            "(type = 'pvc' AND claim_name IS NOT NULL AND namespace IS NOT NULL"
            " AND nfs_server IS NULL AND nfs_path IS NULL)"
            " OR (type = 'nfs' AND nfs_server IS NOT NULL AND nfs_path IS NOT NULL AND claim_name IS NULL)",
            name="ck_storage_binding_target",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_storage.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null = the portal default cluster (mounted kubeconfig), mirroring deployments.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Null = every namespace of the cluster (NFS only; PVCs are namespaced).
    namespace: Mapped[str | None] = mapped_column(String(63), nullable=True)
    type: Mapped[str] = mapped_column(String(8), nullable=False)  # pvc | nfs
    claim_name: Mapped[str | None] = mapped_column(String(253), nullable=True)
    nfs_server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nfs_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sub_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
