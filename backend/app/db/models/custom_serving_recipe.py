import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomServingRecipe(CustomBase):
    """A named, reusable vLLM serving configuration.

    Captures the reusable subset of a model deployment's serving spec (model
    weights, image, compute, vLLM flags, env, placement hints) minus the
    per-instance fields (name, namespace, cluster, ingress, replicas). Applied by
    launching a deployment from it via the Deploy dialog.
    """

    __tablename__ = "custom_serving_recipe"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_path: Mapped[str] = mapped_column(String(512), nullable=False)
    image: Mapped[str] = mapped_column(String(512), nullable=False)
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
    vllm_extra_args: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
