"""An llm-d serving stack deployed for a model via an ArgoCD Application.

The portal stores the desired config; ArgoCD owns the running workloads. Sync/
health status is read live from the Application CR, never persisted here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomLlmdStack(CustomBase):
    __tablename__ = "custom_llmd_stack"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    # The already-running model the EPP router targets (an existing deployment's
    # model_name; the router selects its pods by the llm-ops/model-name label).
    target_model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Which registered ArgoCD connection manages this stack's Application.
    argocd_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_argocd_connection.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="default", server_default="default")
    argo_app_name: Mapped[str] = mapped_column(String(253), nullable=False)
    # EPP / inference-scheduler replica count.
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Standalone-chart routing options (overridable).
    model_server_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="vllm", server_default="vllm"
    )
    target_port: Mapped[int] = mapped_column(Integer, nullable=False, default=8000, server_default="8000")
    # Label selector for the model server pods; null = derive from target_model_name.
    endpoint_selector: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Free-form Helm values deep-merged over the generated base (full control).
    values_override: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    values_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
