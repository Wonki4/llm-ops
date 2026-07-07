"""An llm-d serving stack deployed for a model via an ArgoCD Application.

The portal stores the desired config; ArgoCD owns the running workloads. Sync/
health status is read live from the Application CR, never persisted here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
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
    namespace: Mapped[str] = mapped_column(String(128), nullable=False, default="default", server_default="default")
    argo_app_name: Mapped[str] = mapped_column(String(253), nullable=False)
    # Authoritative Helm values the user edits directly as values.yaml. A thin
    # base (image registry, endpointSelector default) is merged under this at
    # render time; everything else lives here.
    helm_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # The fully-rendered values actually sent to ArgoCD (base + helm_values).
    values_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
