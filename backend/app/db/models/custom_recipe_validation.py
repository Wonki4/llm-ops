"""Durable validation orchestration. Measurement data lives only in benchmark runs."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomRecipeValidation(CustomBase):
    __tablename__ = "custom_recipe_validation"
    __table_args__ = (UniqueConstraint("created_by", "request_key"),)
    # Async serializers must not lazy-load the server-generated updated_at after UPDATE.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(256))
    model_name: Mapped[str] = mapped_column(String(256), index=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_k8s_cluster.id", ondelete="RESTRICT")
    )
    namespace: Mapped[str] = mapped_column(String(128))
    # Execution conditions are immutable after creation; changing them starts a new job.
    conditions: Mapped[dict] = mapped_column(JSONB)
    search_state: Mapped[dict | None] = mapped_column(JSONB)
    request_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="queued")
    message: Mapped[str | None] = mapped_column(Text)
    selected_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomValidationCandidate(CustomBase):
    __tablename__ = "custom_validation_candidate"
    __table_args__ = (UniqueConstraint("validation_id", "position"), UniqueConstraint("validation_id", "request_key"))

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_recipe_validation.id", ondelete="CASCADE"), index=True
    )
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_serving_recipe.id", ondelete="SET NULL")
    )
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    position: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    weights: Mapped[str] = mapped_column(String(512))
    gpu_count: Mapped[int] = mapped_column(Integer)
    # Recipe/environment snapshots can contain credentials; never return ciphertext or raw values.
    snapshot_encrypted: Mapped[str] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    assessment: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomValidationAttempt(CustomBase):
    __tablename__ = "custom_validation_attempt"
    __table_args__ = (UniqueConstraint("candidate_id", "number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    validation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_recipe_validation.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_validation_candidate.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(16))  # compare / final
    status: Mapped[str] = mapped_column(String(32), default="preflight")
    stage_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Recovery metadata: deterministic resources, benchmark IDs, preflight asset hashes,
    # original outcome retained while cleanup retries. No benchmark result copies.
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    diagnostics: Mapped[dict | None] = mapped_column(JSONB)
    analysis: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
