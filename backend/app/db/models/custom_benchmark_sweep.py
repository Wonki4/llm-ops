import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomBenchmarkSweep(CustomBase):
    """One benchmark sweep: a grid of `vllm serve` flag combos over a fixed
    load preset, executed as sequential self-serving Jobs (one combo at a
    time). The sweep row owns grouping/ordering only — each combo is an
    ordinary CustomBenchmarkRun linked via run.sweep_id + run.sweep_index."""

    __tablename__ = "custom_benchmark_sweep"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Base target: exactly one of deployment_id (portal template; plain UUID
    # like the run column) or external_source ({cluster_id, namespace,
    # deployment_name}) is set.
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    external_source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    k8s_namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    preset: Mapped[str] = mapped_column(String(32), nullable=False)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False)
    serving_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default="running", index=True
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
