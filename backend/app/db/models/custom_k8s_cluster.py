"""Admin-registered Kubernetes clusters.

Each row stores one cluster's connection config: a pasted kubeconfig (encrypted
at rest) plus the context to select within it. Other menus (deployments,
benchmarks) target a cluster later by its stable ``id``. The portal never returns
the kubeconfig to the client — only the non-secret ``api_server`` summary.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomK8sCluster(CustomBase):
    __tablename__ = "custom_k8s_cluster"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    context: Mapped[str] = mapped_column(String(256), nullable=False)
    namespace: Mapped[str] = mapped_column(
        String(128), nullable=False, default="default", server_default="default"
    )
    kubeconfig_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_server: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Optional default model-weights NFS mount for benchmarks against a raw
    # model_name: server + export path + container mount path. A per-run override
    # (params) takes precedence. All nullable, non-secret.
    default_nfs_server: Mapped[str | None] = mapped_column(String(253), nullable=True)
    default_nfs_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_nfs_mount_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
