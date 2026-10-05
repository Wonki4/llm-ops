"""GPU type profile: one GPU kind on one cluster, as a placement bundle.

Clusters here have no GPU Feature Discovery; nodes carry a hand-managed
label (key configurable per cluster, default ``gpu-type``). A profile maps a
short operator-chosen name (``a100-80g``) to the node label pair, the
tolerations the pool needs and the extended resource key to request, so a
recipe can ask for a GPU *kind* without knowing any cluster's labels.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomGpuProfile(CustomBase):
    __tablename__ = "custom_gpu_profile"
    __table_args__ = (
        UniqueConstraint("cluster_id", "name", name="uq_gpu_profile_cluster_name", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null = the portal default cluster (mounted kubeconfig), mirroring deployments.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Null → the cluster's gpu_label_key.
    label_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    label_value: Mapped[str] = mapped_column(String(128), nullable=False)
    gpu_resource_key: Mapped[str] = mapped_column(
        String(128), nullable=False, default="nvidia.com/gpu", server_default="nvidia.com/gpu"
    )
    tolerations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    vram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
