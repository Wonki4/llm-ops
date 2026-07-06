"""LiteLLM registrations for externally-discovered vLLM/SGLang servings.

Discovery itself is a live cluster scan (no rows); this table only remembers
which discovered serving was registered with LiteLLM, keyed by
(cluster, namespace, deployment name).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomExternalServing(CustomBase):
    __tablename__ = "custom_external_serving"
    __table_args__ = (
        UniqueConstraint(
            "cluster_id", "namespace", "deployment_name",
            name="uq_external_serving_target",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null = portal default kubeconfig, mirroring custom_model_deployment.cluster_id.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    deployment_name: Mapped[str] = mapped_column(String(253), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_base: Mapped[str] = mapped_column(String(512), nullable=False)
    litellm_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    registered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
