"""API Key management endpoints."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser
from app.db.session import get_db

router = APIRouter(prefix="/api/keys", tags=["keys"])


class CreateKeyRequest(BaseModel):
    team_id: str
    key_alias: str | None = None
    models: list[str] | None = None
    max_budget: float | None = None
    budget_duration: str | None = Field(None, description="e.g. '30d', '7d', '1h'")


@router.post("")
async def create_key(
    body: CreateKeyRequest,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Create a new API key linked to the user's 사번 and team."""
    result = await litellm.generate_key(
        user_id=user.user_id,
        team_id=body.team_id,
        key_alias=body.key_alias or f"{user.user_id}-{body.team_id}",
        models=body.models,
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
    )
    return result


@router.get("")
async def list_my_keys(
    team_id: str | None = None,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List current user's API keys, optionally filtered by team."""
    query = (
        "SELECT token, key_name, key_alias, team_id, user_id, "
        "       spend, max_budget, budget_duration, budget_reset_at, "
        "       models, expires, created_at "
        'FROM "LiteLLM_VerificationToken" '
        "WHERE user_id = :user_id "
    )
    params: dict = {"user_id": user.user_id}
    if team_id:
        query += "AND team_id = :team_id "
        params["team_id"] = team_id
    query += "ORDER BY created_at DESC"

    result = await db.execute(text(query), params)
    keys = [
        {
            "token": k["token"],
            "key_name": k["key_name"],
            "key_alias": k["key_alias"],
            "team_id": k["team_id"],
            "user_id": k["user_id"],
            "spend": float(k["spend"]),
            "max_budget": k["max_budget"],
            "budget_duration": k["budget_duration"],
            "budget_reset_at": (k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None),
            "models": list(k["models"] or []),
            "expires": k["expires"].isoformat() if k["expires"] else None,
            "created_at": k["created_at"].isoformat() if k["created_at"] else None,
        }
        for k in result.mappings()
    ]
    return {"keys": keys}


@router.delete("/{key_hash}")
async def delete_key(
    key_hash: str,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Delete an API key (user can only delete their own keys)."""
    # Verify ownership
    key_info = await litellm.get_key_info(key_hash)
    info = key_info.get("info", key_info)
    if info.get("user_id") != user.user_id:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own keys")
    return await litellm.delete_key(key_hash)
