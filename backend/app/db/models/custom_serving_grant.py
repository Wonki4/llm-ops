import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomServingGrant(CustomBase):
    __tablename__ = "custom_serving_grant"
    __table_args__ = (
        UniqueConstraint("team_id", "cluster_id", "namespace", name="uq_serving_grant_scope",
                         postgresql_nulls_not_distinct=True),
        CheckConstraint("role IN ('viewer', 'operator')", name="ck_serving_grant_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String, nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("custom_k8s_cluster.id", ondelete="CASCADE"))
    namespace: Mapped[str] = mapped_column(String(63))
    role: Mapped[str] = mapped_column(String(16))


class CustomServingGrantEvent(CustomBase):
    __tablename__ = "custom_serving_grant_event"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String)
    cluster_id: Mapped[uuid.UUID | None]
    namespace: Mapped[str] = mapped_column(String(63))
    role: Mapped[str | None] = mapped_column(String(16))  # NULL records a revocation
    actor: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
