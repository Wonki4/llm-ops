"""User session/profile endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_settings import personal_keys_beta_enabled
from app.auth.deps import get_current_user
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db
from app.services.personal_access import grant_state, has_grant

router = APIRouter(prefix="/api/me", tags=["me"])

SUPPORTED_LOCALES = {"ko", "en"}


class UpdateLocaleRequest(BaseModel):
    locale: str = Field(..., min_length=2, max_length=8)


@router.get("")
async def get_me(
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Get current user profile (+ the personal-key grant and limits on the LiteLLM user row)."""
    result = await litellm_db.execute(
        text(
            "SELECT spend, max_budget, models, tpm_limit, rpm_limit, budget_duration "
            'FROM "LiteLLM_UserTable" '
            "WHERE user_id = :user_id"
        ),
        {"user_id": user.user_id},
    )
    row = result.mappings().first()
    grant = list(row["models"] or []) if row else []
    beta = await personal_keys_beta_enabled(db)

    # Determine effective role: super_user > team_admin > user
    role = user.global_role.value
    if role != "super_user":
        admin_check = await litellm_db.execute(
            text('SELECT 1 FROM "LiteLLM_TeamTable" WHERE :user_id = ANY(COALESCE(admins, ARRAY[]::text[])) LIMIT 1'),
            {"user_id": user.user_id},
        )
        if admin_check.scalar_one_or_none() is not None:
            role = "team_admin"

    return {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": role,
        "locale": user.locale,
        "spend": float(row["spend"]) if row else 0,
        "max_budget": row["max_budget"] if row else None,
        "budget_duration": row["budget_duration"] if row else None,
        "tpm_limit": row["tpm_limit"] if row else None,
        "rpm_limit": row["rpm_limit"] if row else None,
        # Personal keys (Beta): what the user row grants and whether a key can be minted now.
        "models": grant,
        "personal_access": grant_state(grant),
        "personal_keys_beta_enabled": beta,
        "personal_keys_enabled": beta and has_grant(grant),
    }


@router.patch("/locale")
async def update_locale(
    body: UpdateLocaleRequest,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update the current user's UI locale preference."""
    if body.locale not in SUPPORTED_LOCALES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported locale. Supported: {sorted(SUPPORTED_LOCALES)}",
        )
    await db.execute(
        text("UPDATE custom_users SET locale = :locale WHERE user_id = :user_id"),
        {"locale": body.locale, "user_id": user.user_id},
    )
    return {"locale": body.locale}
