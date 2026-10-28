"""Gateway SSO keys: one portal-minted LiteLLM key per (user, team-or-personal).

When a web app forwards a user's Keycloak token to the inference gateway, the
gateway swaps it for this key. The key is minted on first use and afterwards
rebuilt from its metadata (the raw key is never stored). Its spend therefore
lands on the user (personal) or on the team, exactly like a hand-made key.

A blocked or expired SSO key is returned as-is so LiteLLM rejects the call:
an admin block must not be bypassed by silently minting a fresh key. Deleting
the key, on the other hand, lets the next request mint a new one.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.litellm import LiteLLMClient
from app.services.personal_access import PERSONAL_KEY_TYPE
from app.services.portal_keys import issue_portal_key, reconstruct_sk_key

logger = logging.getLogger(__name__)

SSO_ISSUED_VIA = "gateway_sso"  # metadata.issued_via on gateway SSO keys
SSO_DISPLAY_ALIAS = "SSO (gateway)"


async def _find_sso_key(litellm_db: AsyncSession, user_id: str, team_id: str | None) -> str | None:
    rows = await litellm_db.execute(
        text(
            'SELECT user_id, team_id, metadata FROM "LiteLLM_VerificationToken" '
            "WHERE user_id = :user_id AND team_id IS NOT DISTINCT FROM :team_id "
            "AND metadata->>'issued_via' = :issued_via "
            "ORDER BY (metadata->>'sk_key_id')::bigint DESC"
        ),
        {"user_id": user_id, "team_id": team_id, "issued_via": SSO_ISSUED_VIA},
    )
    for row in rows.mappings():
        key = reconstruct_sk_key(row["user_id"], row["team_id"], row["metadata"])
        if key:
            return key
    return None


async def get_or_issue_sso_key(
    litellm: LiteLLMClient,
    db: AsyncSession,
    litellm_db: AsyncSession,
    *,
    user_id: str,
    team_id: str | None,
) -> str:
    """The user's gateway SSO key for ``team_id`` (None = personal), minting it once.

    Concurrent first requests are serialised with a transaction-scoped advisory
    lock on the portal DB, so a user never ends up with duplicate SSO keys.
    The portal transaction is committed (or rolled back) here to release the lock.
    """
    existing = await _find_sso_key(litellm_db, user_id, team_id)
    if existing:
        return existing

    lock_name = f"gateway_sso:{user_id}:{team_id or ''}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": lock_name})
    try:
        # Another request may have minted it while we waited for the lock.
        existing = await _find_sso_key(litellm_db, user_id, team_id)
        if existing is None:
            metadata: dict = {"display_alias": SSO_DISPLAY_ALIAS, "issued_via": SSO_ISSUED_VIA}
            if team_id is None:
                metadata["key_type"] = PERSONAL_KEY_TYPE
            existing, _ = await issue_portal_key(litellm, db, user_id=user_id, team_id=team_id, metadata=metadata)
            logger.info("Minted gateway SSO key for user=%s team=%s", user_id, team_id or "-")
    except Exception:
        await db.rollback()  # releases the advisory lock
        raise
    await db.commit()  # persists the key sequence and releases the advisory lock
    return existing
