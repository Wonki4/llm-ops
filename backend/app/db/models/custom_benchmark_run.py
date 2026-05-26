import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
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
