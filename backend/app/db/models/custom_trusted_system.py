"""Trusted system model.

Allows specific upstream systems to authenticate to the inference gateway
without a LiteLLM key — they present a system id + shared secret, and the
gateway resolves them to a per-system LiteLLM virtual key.  The end user is
identified separately (e.g. via the ``emp-no`` header → LiteLLM end-user).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomTrustedSystem(CustomBase):
    __tablename__ = "custom_trusted_systems"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Value sent by the client in the system-id header (e.g. "payroll").
    system_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # SHA-256 hex digest of the shared secret (never store the raw secret).
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # LiteLLM virtual key (sk-...) injected for requests from this system.
    litellm_key: Mapped[str] = mapped_column(String(256), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
