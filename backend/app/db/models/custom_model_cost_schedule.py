import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomModelCostSchedule(CustomBase):
    """Time-of-day overrides for per-model token costs.

    days_of_week uses ISO weekday integers (1=Mon..7=Sun). The active window is
    [hour_start_utc, hour_end_utc) in UTC; when hour_end_utc <= hour_start_utc the
    window spans midnight (e.g. 22→6 means 22:00–05:59 UTC). When multiple rules
    match the current moment, the highest priority wins.
    """

    __tablename__ = "custom_model_cost_schedule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), nullable=False)
    hour_start_utc: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hour_end_utc: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    input_cost_per_token: Mapped[float] = mapped_column(Float, nullable=False)
    output_cost_per_token: Mapped[float] = mapped_column(Float, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
