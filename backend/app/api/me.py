"""User session/profile endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_litellm_db

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("")
async def get_me(
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Get current user profile."""
    result = await litellm_db.execute(
        text(
            "SELECT spend, max_budget "
            'FROM "LiteLLM_UserTable" '
            "WHERE user_id = :user_id"
        ),
        {"user_id": user.user_id},
    )
    row = result.mappings().first()

    return {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.global_role.value,
        "spend": float(row["spend"]) if row else 0,
        "max_budget": row["max_budget"] if row else None,
    }
