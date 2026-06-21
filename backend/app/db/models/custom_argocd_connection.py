"""A registered ArgoCD connection (server URL + API token).

The token is Fernet-encrypted at rest (``app/services/crypto.py``) and never
returned to the client — list/get expose only a ``has_token`` flag. The portal
talks to ArgoCD's REST API to manage Applications for llm-d stacks.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class CustomArgocdConnection(CustomBase):
    __tablename__ = "custom_argocd_connection"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    server_url: Mapped[str] = mapped_column(String(512), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    insecure_skip_verify: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
