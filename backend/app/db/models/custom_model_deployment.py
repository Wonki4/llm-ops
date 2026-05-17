import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomModelDeployment(CustomBase):
    """Admin-managed K8s LLM deployment metadata.

    One row drives one Deployment + Service + Ingress in the configured
    namespace. The worker reconciles K8s state into `status`/`ready_replicas`.
    LiteLLM-side model ids live on the catalog rows attached to this deployment
    (1 deployment → N catalog rows, joined by custom_model_catalog.deployment_id).
    """

    __tablename__ = "custom_model_deployment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="default", server_default="default")
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gpu_resource_key: Mapped[str] = mapped_column(
        String(128), nullable=False, default="nvidia.com/gpu", server_default="nvidia.com/gpu"
    )
    cpu_request: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cpu_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memory_request: Mapped[str | None] = mapped_column(String(32), nullable=True)
    memory_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tolerations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    pvc_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pvc_mount_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_path: Mapped[str] = mapped_column(String(512), nullable=False)
    vllm_extra_args: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ingress_host: Mapped[str] = mapped_column(String(256), nullable=False)
    ingress_path: Mapped[str] = mapped_column(String(256), nullable=False, default="/", server_default="/")
    ingress_class: Mapped[str] = mapped_column(
        String(64), nullable=False, default="nginx", server_default="nginx"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="Pending", server_default="Pending")
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    service_cluster_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
