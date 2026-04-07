"""API Key management endpoints."""

import asyncio
import logging
import time

from httpx import HTTPStatusError
from jose import jwt
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db

router = APIRouter(prefix="/api/keys", tags=["keys"])

_KEY_JWT_SECRET = "litellm-portal-key-sign"  # signing secret for key JWTs


async def _next_key_id(db: AsyncSession) -> int:
    """Get next sequential key ID starting from 10000."""
    result = await db.execute(
        text("SELECT COALESCE(MAX(key_seq), 9999) + 1 FROM custom_key_sequence")
    )
    next_id = result.scalar()
    if next_id is None:
        next_id = 10000
    await db.execute(
        text("INSERT INTO custom_key_sequence (key_seq) VALUES (:seq)"),
        {"seq": next_id},
    )
    return next_id


def _generate_sk_jwt(key_id: int, team_id: str, user_id: str, iat: int | None = None) -> str:
    """Generate sk- prefixed JWT key."""
    if iat is None:
        iat = int(time.time())
    payload = {
        "keyId": key_id,
        "prjId": team_id,
        "keyType": "PRJ",
        "regUserId": user_id,
        "iat": iat,
    }
    token = jwt.encode(payload, _KEY_JWT_SECRET, algorithm="HS256")
    return f"sk-{token}"


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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new API key with sk- JWT format. Retries on 500 errors."""
    # Read default TPM/RPM from portal settings
    settings_result = await db.execute(
        text("SELECT key, value FROM custom_portal_settings WHERE key IN ('default_tpm_limit', 'default_rpm_limit')")
    )
    portal_settings = {r["key"]: int(r["value"]) for r in settings_result.mappings()}
    tpm_limit = portal_settings.get("default_tpm_limit", 100000)
    rpm_limit = portal_settings.get("default_rpm_limit", 1000)

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        key_id = await _next_key_id(db)
        iat = int(time.time())
        sk_key = _generate_sk_jwt(key_id, body.team_id, user.user_id, iat=iat)

        try:
            result = await litellm.generate_key(
                user_id=user.user_id,
                team_id=body.team_id,
                key_alias=f"{user.user_id}-{key_id}",
                models=body.models,
                max_budget=body.max_budget,
                budget_duration=body.budget_duration,
                key=sk_key,
                tpm_limit=tpm_limit,
                rpm_limit=rpm_limit,
                metadata={"sk_key_id": key_id, "sk_iat": iat, "display_alias": body.key_alias or ""},
            )
            return result
        except HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_error = e
                logger.warning("Key generation failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_error = e
            logger.warning("Key generation failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))
                continue
            raise

    raise HTTPException(status_code=502, detail=f"Key generation failed after {max_retries} attempts: {last_error}")


@router.get("")
async def list_my_keys(
    team_id: str | None = None,
    user: CustomUser = Depends(get_current_user),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """List current user's API keys, optionally filtered by team."""
    query = (
        "SELECT token, key_name, key_alias, team_id, user_id, "
        "       spend, max_budget, budget_duration, budget_reset_at, "
        "       models, expires, created_at, metadata "
        'FROM "LiteLLM_VerificationToken" '
        "WHERE user_id = :user_id "
    )
    params: dict = {"user_id": user.user_id}
    if team_id:
        query += "AND team_id = :team_id "
        params["team_id"] = team_id
    query += "ORDER BY created_at DESC"

    result = await litellm_db.execute(text(query), params)
    keys = [
        {
            "token": k["token"],
            "key_name": k["key_name"],
            "key_alias": (k["metadata"] or {}).get("display_alias") or k["key_alias"],
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

    metadata = row["metadata"] or {}
    sk_key_id = metadata.get("sk_key_id")
    sk_iat = metadata.get("sk_iat")
    if sk_key_id is None or sk_iat is None:
        raise HTTPException(status_code=404, detail="이 키는 재구성 정보가 없습니다. (이전에 생성된 키)")

    sk_key = _generate_sk_jwt(sk_key_id, row["team_id"], row["user_id"], iat=sk_iat)
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
    from fastapi import HTTPException, status as http_status

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
