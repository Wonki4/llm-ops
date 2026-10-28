"""API Key management endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.api.key_limits import effective_model_limits
from app.api.portal_settings import personal_keys_beta_enabled
from app.api.teams import _get_hidden_team_settings
from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db
from app.services.personal_access import (
    PERSONAL_KEY_TYPE,
    access_groups_from_model_info,
    has_grant,
    key_models_allowed,
)
from app.services.portal_keys import KEY_JWT_SECRET, generate_sk_jwt, issue_portal_key, reconstruct_sk_key

router = APIRouter(prefix="/api/keys", tags=["keys"])

# Kept under their historical private names for existing importers/tests.
_KEY_JWT_SECRET = KEY_JWT_SECRET
_generate_sk_jwt = generate_sk_jwt


class CreateKeyRequest(BaseModel):
    team_id: str | None = None  # None → a personal key (Beta): governed by the user row, not a team
    key_alias: str
    models: list[str] | None = None
    max_budget: float | None = None
    budget_duration: str | None = Field(None, description="e.g. '30d', '7d', '1h'")


async def require_personal_access(db: AsyncSession, litellm_db: AsyncSession, user_id: str):
    """403 unless personal keys are on (Beta) and the user row carries a grant.

    Returns the user row (``models``, ``max_budget``).
    """
    if not await personal_keys_beta_enabled(db):
        raise HTTPException(status_code=403, detail="개인 키(Beta)가 꺼져 있습니다. 관리자에게 문의하세요.")
    row = (
        await litellm_db.execute(
            text('SELECT models, max_budget FROM "LiteLLM_UserTable" WHERE user_id = :user_id'),
            {"user_id": user_id},
        )
    ).mappings().first()
    if not has_grant(list(row["models"] or []) if row else []):
        raise HTTPException(status_code=403, detail="관리자가 개인 키 권한을 부여해야 합니다.")
    return row


async def _personal_key_guard(
    body: CreateKeyRequest, user: CustomUser, litellm: LiteLLMClient, db: AsyncSession, litellm_db: AsyncSession
) -> None:
    """Personal keys need the beta switch on, a grant on the user row, and a
    key model list inside that grant (LiteLLM would 403 at call time anyway)."""
    row = await require_personal_access(db, litellm_db, user.user_id)
    grant = list(row["models"] or []) if row else []
    if body.models:
        groups = access_groups_from_model_info(await litellm.get_model_info())
        outside = key_models_allowed(body.models, grant, access_groups=groups)
        if outside:
            raise HTTPException(
                status_code=400, detail=f"개인 키 권한 밖의 모델입니다: {', '.join(outside)}"
            )
    user_budget = row["max_budget"] if row else None
    if body.max_budget is not None and user_budget is not None and body.max_budget > float(user_budget):
        raise HTTPException(status_code=400, detail=f"키 예산은 개인 예산({user_budget})을 넘을 수 없습니다.")


@router.post("")
async def create_key(
    body: CreateKeyRequest,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Create a new API key with sk- JWT format. Retries on 500 errors."""
    personal = not body.team_id
    if personal:
        await _personal_key_guard(body, user, litellm, db, litellm_db)

    metadata: dict = {"display_alias": body.key_alias}
    if personal:
        # Tracking marker for spend logs / key lists. (LiteLLM key `tags` are an
        # Enterprise feature — /key/generate 403s on them — so metadata it is.)
        metadata["key_type"] = PERSONAL_KEY_TYPE

    sk_key, result = await issue_portal_key(
        litellm,
        db,
        user_id=user.user_id,
        team_id=body.team_id,
        metadata=metadata,
        models=body.models,
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
    )
    # Return key without sk- prefix (sensitive prefix must not be exposed)
    result["key"] = sk_key.removeprefix("sk-")
    return result


@router.get("")
async def list_my_keys(
    team_id: str | None = None,
    user: CustomUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List current user's API keys, optionally filtered by team."""
    query = (
        "SELECT v.token, v.key_name, v.key_alias, v.team_id, v.user_id, "
        "       v.spend, v.max_budget, v.budget_duration, v.budget_reset_at, "
        "       v.models, v.expires, v.created_at, v.metadata, v.tpm_limit, v.rpm_limit, "
        "       t.metadata AS team_metadata "
        'FROM "LiteLLM_VerificationToken" v '
        'LEFT JOIN "LiteLLM_TeamTable" t ON v.team_id = t.team_id '
        "WHERE v.user_id = :user_id "
    )
    params: dict = {"user_id": user.user_id}
    if team_id:
        query += "AND v.team_id = :team_id "
        params["team_id"] = team_id
    query += "ORDER BY v.created_at DESC"

    result = await litellm_db.execute(text(query), params)
    keys = [
        {
            "token": k["token"],
            "key_name": k["key_name"],
            "key_alias": (k["metadata"] or {}).get("display_alias", ""),
            "team_id": k["team_id"],
            "personal": k["team_id"] is None,
            "user_id": k["user_id"],
            "spend": float(k["spend"]),
            "max_budget": k["max_budget"],
            "budget_duration": k["budget_duration"],
            "budget_reset_at": (k["budget_reset_at"].isoformat() if k["budget_reset_at"] else None),
            "models": list(k["models"] or []),
            "expires": k["expires"].isoformat() if k["expires"] else None,
            "created_at": k["created_at"].isoformat() if k["created_at"] else None,
            "tpm_limit": k["tpm_limit"],
            "rpm_limit": k["rpm_limit"],
            **effective_model_limits(k["metadata"], k["team_metadata"]),
        }
        for k in result.mappings()
    ]

    # Only STRICT-hidden teams take members' keys with them; default-hidden
    # teams stay usable/visible for their members.
    if user.global_role != GlobalRole.SUPER_USER:
        _, strict = await _get_hidden_team_settings(db)
        keys = [k for k in keys if k["team_id"] not in strict]

    return {"keys": keys}


@router.get("/{key_hash}/reveal")
async def reveal_key(
    key_hash: str,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Reconstruct and return the sk-JWT key for the user to copy."""
    result = await litellm_db.execute(
        text(
            'SELECT user_id, team_id, metadata '
            'FROM "LiteLLM_VerificationToken" WHERE token = :token'
        ),
        {"token": key_hash},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="You can only reveal your own keys")

    sk_key = reconstruct_sk_key(row["user_id"], row["team_id"], row["metadata"])
    if sk_key is None:
        raise HTTPException(status_code=404, detail="이 키는 재구성 정보가 없습니다. (이전에 생성된 키)")

    # Strip sk- prefix for user-facing display
    user_key = sk_key.removeprefix("sk-")
    return {"key": user_key}


@router.delete("/{key_hash}")
async def delete_key(
    key_hash: str,
    user: CustomUser = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Delete an API key (user can only delete their own keys)."""
    from fastapi import HTTPException
    from fastapi import status as http_status

    result = await litellm_db.execute(
        text('SELECT user_id FROM "LiteLLM_VerificationToken" WHERE token = :token'),
        {"token": key_hash},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Key not found")
    if row["user_id"] != user.user_id:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="You can only delete your own keys")
    return await litellm.delete_key(key_hash)
