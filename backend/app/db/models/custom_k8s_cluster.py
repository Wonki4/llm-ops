import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomK8sCluster(CustomBase):
    """Registered Kubernetes cluster the portal can deploy LLMs to.

    Each `custom_model_deployment` row carries a `cluster_id` FK pointing here.
    `kubeconfig_content` is stored verbatim today; envelope encryption is a
    follow-up before exposing this to multi-tenant operators.
    """

    __tablename__ = "custom_k8s_cluster"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    kubeconfig_content: Mapped[str] = mapped_column(Text, nullable=False)
    default_namespace: Mapped[str] = mapped_column(
        String(128), nullable=False, default="default", server_default="default"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
