import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomModelDeploymentEvent(CustomBase):
    """Status transition log for K8s-managed deployments.

    Reconciler writes a row whenever a deployment's observed status changes
    or it surfaces a notable condition (Ready, Unhealthy, Failed, LitellmRegistered).
    `severity=warning/error` rows also fan out to slack_webhook_url once.
    """

    __tablename__ = "custom_model_deployment_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_model_deployment.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info", server_default="info")
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
