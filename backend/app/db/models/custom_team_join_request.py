import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class JoinRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RequestType(str, enum.Enum):
    JOIN = "join"
    BUDGET = "budget"


class CustomTeamJoinRequest(CustomBase):
    """Tracks team join requests and budget increase requests."""

    __tablename__ = "custom_team_join_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # 사번
    team_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    team_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="join", index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[JoinRequestStatus] = mapped_column(
        Enum(
            JoinRequestStatus,
            name="custom_join_request_status",
            create_constraint=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=JoinRequestStatus.PENDING,
        server_default="pending",
        index=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
