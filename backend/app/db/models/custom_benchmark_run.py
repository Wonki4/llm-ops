import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomBenchmarkRun(CustomBase):
    """One benchmark execution against a LiteLLM-registered model.

    Spawned as a K8s Job by `backend/app/api/benchmarks.py` and polled by the
    `reconcile_benchmarks` worker loop. `params` stores the user-supplied
    arguments verbatim so a run can be reproduced; `result` stores the
    parsed metrics emitted by the runner image.
    """

    __tablename__ = "custom_benchmark_run"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    tool: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Optional human metadata for identifying/comparing runs.
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which registered K8s cluster the run executes on. Null = portal default
    # (mounted kubeconfig). RESTRICT: a cluster can't be deleted while in use.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # When the run targets a portal-managed serving deployment (vLLM/SGLang),
    # `deployment_id` links to custom_model_deployment and `serving_snapshot`
    # freezes that serving's config (image, args, env, resources, gpu, node
    # selector) at run time so results stay comparable even if the deployment
    # is later edited or deleted. Null for legacy "hit the LiteLLM alias" runs.
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    serving_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Ephemeral mode: the run provisions a throwaway serving (cloned from the
    # deployment named by deployment_id, with serving_snapshot as the effective
    # spec), benchmarks it, then tears it down. status starts at `provisioning`.
    # serving_k8s_name is the base name of the temp Deployment/Service; bench_image
    # is the runner image the reconciler uses once the serving is ready.
    ephemeral: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    serving_k8s_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bench_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    serving_torn_down: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending", index=True
    )
    k8s_job_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    k8s_namespace: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
