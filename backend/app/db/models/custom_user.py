import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import CustomBase


class GlobalRole(str, enum.Enum):
    USER = "user"
    SUPER_USER = "super_user"


class CustomUser(CustomBase):
    """Maps Keycloak user (사번) to LiteLLM user_id and tracks global role."""

    __tablename__ = "custom_users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # 사번 = Keycloak username
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    global_role: Mapped[GlobalRole] = mapped_column(
        Enum(
            GlobalRole,
            name="custom_global_role",
            create_constraint=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=GlobalRole.USER,
        server_default="user",
    )
    litellm_user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)  # LiteLLM user_id if different
    locale: Mapped[str] = mapped_column(String(8), default="ko", server_default="ko")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
