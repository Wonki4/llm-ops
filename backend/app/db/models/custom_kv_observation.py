"""One durable engine observation per Pod container incarnation."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomKVObservation(CustomBase):
    __tablename__ = "custom_kv_observation"
    __table_args__ = (
        Index("ix_kv_observation_fingerprint", "fingerprint", "last_seen"),
        Index("ix_kv_observation_attempt", "attempt_id"),
    )

    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_model_deployment.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    pod_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    restart: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("custom_validation_attempt.id", ondelete="CASCADE")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    pod_name: Mapped[str] = mapped_column(String(253))
    engine: Mapped[str] = mapped_column(String(16))
    image: Mapped[str] = mapped_column(String(512))
    image_id: Mapped[str | None] = mapped_column(String(768))
    gpu_type: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[str | None] = mapped_column(String(256))
    capacity_tokens: Mapped[int | None] = mapped_column(BigInteger)
    budget_bytes: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[dict] = mapped_column(JSONB)
