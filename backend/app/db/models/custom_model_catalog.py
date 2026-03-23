import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class ModelStatus(str, enum.Enum):
    TESTING = "testing"
    PRERELEASE = "prerelease"
    LTS = "lts"
    DEPRECATING = "deprecating"
    DEPRECATED = "deprecated"


class CustomModelCatalog(CustomBase):
    """Extended model catalog with lifecycle management.

    Links to LiteLLM model_name but stores additional metadata
    like descriptions, status lifecycle, and per-status scheduled dates.
    Cost info comes from LiteLLM model_info (not stored in catalog).
    """

    __tablename__ = "custom_model_catalog"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"testing": "2026-01-15", "lts": "2026-03-01", ...}
    status: Mapped[ModelStatus] = mapped_column(
        Enum(
            ModelStatus,
            name="custom_model_status",
            create_constraint=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=ModelStatus.TESTING,
        server_default="testing",
        index=True,
    )
    visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    status_change_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
