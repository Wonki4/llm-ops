"""Portal-issued LiteLLM keys: the sk-JWT format, defaults, and minting.

Every key the portal mints is an ``sk-`` prefixed HS256 JWT whose claims can be
rebuilt from the key's metadata (``sk_key_id`` / ``sk_iat``), so the raw key is
never stored: LiteLLM keeps only its hash, and the portal reconstructs the key
when it needs it (reveal, gateway SSO).
"""

import asyncio
import json
import logging
import time

from fastapi import HTTPException
from httpx import HTTPStatusError
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.litellm import LiteLLMClient

logger = logging.getLogger(__name__)

KEY_JWT_SECRET = "litellm-portal-key-sign"  # signing secret for key JWTs

_MAX_RETRIES = 3


async def next_key_id(db: AsyncSession) -> int:
    """Get next sequential key ID starting from 10000."""
    result = await db.execute(text("SELECT COALESCE(MAX(key_seq), 9999) + 1 FROM custom_key_sequence"))
    next_id = result.scalar()
    if next_id is None:
        next_id = 10000
    await db.execute(
        text("INSERT INTO custom_key_sequence (key_seq) VALUES (:seq)"),
        {"seq": next_id},
    )
    return next_id


def generate_sk_jwt(key_id: int, team_id: str | None, user_id: str, iat: int | None = None) -> str:
    """Generate sk- prefixed JWT key with sub containing stringified payload.

    Personal (team-less) keys carry ``prjId: null`` and ``keyType: "USR"``;
    consumers of the claim (the budget MCP) treat a null project as "no team".
    """
    if iat is None:
        iat = int(time.time())
    inner = {
        "keyId": key_id,
        "prjId": team_id,
        "keyType": "PRJ" if team_id else "USR",
        "regUserId": user_id,
        "iat": iat,
    }
    payload = {"sub": json.dumps(inner, separators=(",", ":"))}
    token = jwt.encode(payload, KEY_JWT_SECRET, algorithm="HS256")
    return f"sk-{token}"


def reconstruct_sk_key(user_id: str, team_id: str | None, metadata: dict | None) -> str | None:
    """Rebuild a portal key from its LiteLLM row, or None for keys minted elsewhere."""
    metadata = metadata or {}
    sk_key_id = metadata.get("sk_key_id")
    sk_iat = metadata.get("sk_iat")
    if sk_key_id is None or sk_iat is None:
        return None
    return generate_sk_jwt(sk_key_id, team_id, user_id, iat=sk_iat)


async def default_rate_limits(db: AsyncSession, team_id: str | None) -> tuple[int, int]:
    """Default (TPM, RPM) for a new key: team-scoped values, else the global defaults.

    Personal keys have no team override, so only the global defaults apply.
    """
    team_tpm_key = f"team:{team_id}:default_tpm_limit"
    team_rpm_key = f"team:{team_id}:default_rpm_limit"
    settings_result = await db.execute(
        text(
            "SELECT key, value FROM custom_portal_settings "
            "WHERE key IN ('default_tpm_limit', 'default_rpm_limit', :team_tpm, :team_rpm)"
        ),
        {"team_tpm": team_tpm_key, "team_rpm": team_rpm_key},
    )
    portal_settings = {r["key"]: int(r["value"]) for r in settings_result.mappings()}
    tpm_limit = portal_settings.get(team_tpm_key, portal_settings.get("default_tpm_limit", 100000))
    rpm_limit = portal_settings.get(team_rpm_key, portal_settings.get("default_rpm_limit", 1000))
    return tpm_limit, rpm_limit


async def issue_portal_key(
    litellm: LiteLLMClient,
    db: AsyncSession,
    *,
    user_id: str,
    team_id: str | None,
    metadata: dict,
    models: list[str] | None = None,
    max_budget: float | None = None,
    budget_duration: str | None = None,
) -> tuple[str, dict]:
    """Mint an sk-JWT key in LiteLLM. Returns ``(sk_key, litellm_result)``.

    ``metadata`` is extended with the reconstruction fields (``sk_key_id``,
    ``sk_iat``). Retries on LiteLLM 5xx / transport errors.
    """
    tpm_limit, rpm_limit = await default_rate_limits(db, team_id)
    last_error: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        key_id = await next_key_id(db)
        iat = int(time.time())
        sk_key = generate_sk_jwt(key_id, team_id, user_id, iat=iat)
        try:
            result = await litellm.generate_key(
                user_id=user_id,
                team_id=team_id,
                key_alias=f"{user_id}-{key_id}",
                models=models,
                max_budget=max_budget,
                budget_duration=budget_duration,
                key=sk_key,
                tpm_limit=tpm_limit,
                rpm_limit=rpm_limit,
                metadata={**metadata, "sk_key_id": key_id, "sk_iat": iat},
            )
            return sk_key, result
        except HTTPStatusError as e:
            if e.response.status_code >= 500:
                last_error = e
                logger.error("Key generation failed (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, e, exc_info=True)
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_error = e
            logger.warning("Key generation failed (attempt %d/%d): %s", attempt + 1, _MAX_RETRIES, e)
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(1 * (attempt + 1))
                continue
            raise

    raise HTTPException(status_code=502, detail=f"Key generation failed after {_MAX_RETRIES} attempts: {last_error}")
