"""User provisioning shared by every way a Keycloak identity enters the portal.

The browser login callback, the bearer/session dependency, and the inference
gateway's SSO token exchange all need the same two things: a portal user row
(``custom_users``, carrying the global role) and a LiteLLM user row placed in
the default teams.
"""

import json
import logging

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.auth.jwt import TokenPayload
from app.config import settings
from app.db.models.custom_user import CustomUser, GlobalRole

logger = logging.getLogger(__name__)


def canonical_user_id(token: TokenPayload) -> str:
    """The portal's user id for a token: ``preferred_username`` upper-cased."""
    return token.preferred_username.upper() if token.preferred_username else ""


def _is_super(token: TokenPayload) -> bool:
    all_roles = (token.realm_roles or []) + (token.client_roles or [])
    user_groups = token.groups or []
    return settings.super_user_role in all_roles or bool(
        settings.admin_groups and set(user_groups) & set(settings.admin_groups)
    )


class IdentityMismatchError(Exception):
    """The token's Keycloak account is not the one pinned to this portal user id."""

    def __init__(self, user_id: str):
        super().__init__(user_id)
        self.user_id = user_id


async def upsert_portal_user(db: AsyncSession, token: TokenPayload) -> CustomUser:
    """Return the portal user for ``token``, creating it and syncing the role.

    Raises ``IdentityMismatchError`` when sub pinning is enforced and the
    token's Keycloak account is not the one pinned to this user id.
    """
    uid = canonical_user_id(token)
    pinning = settings.keycloak_sub_pinning
    result = await db.execute(select(CustomUser).where(CustomUser.user_id == uid))
    user = result.scalar_one_or_none()
    is_super = _is_super(token)

    if not user:
        stmt = pg_insert(CustomUser).values(
            user_id=uid,
            email=token.email,
            display_name=token.name,
            global_role=GlobalRole.SUPER_USER if is_super else GlobalRole.USER,
            keycloak_sub=token.sub if pinning != "off" else None,
        ).on_conflict_do_nothing(index_elements=["user_id"])
        await db.execute(stmt)
        await db.flush()

        # Re-fetch to get the ORM object (a concurrent first login may have won the insert)
        result = await db.execute(select(CustomUser).where(CustomUser.user_id == uid))
        user = result.scalar_one()

    if pinning != "off":
        await _check_sub_pin(db, user, token, enforce=pinning == "enforce")

    new_role = GlobalRole.SUPER_USER if is_super else user.global_role
    if user.global_role != new_role:
        user.global_role = new_role
        await db.flush()
    return user


async def _check_sub_pin(db: AsyncSession, user: CustomUser, token: TokenPayload, *, enforce: bool) -> None:
    """Pin ``user`` to ``token.sub`` on first sight, then require it to match."""
    if not token.sub:
        if enforce:
            logger.error("Token for %s has no 'sub' (missing `basic` client scope?); rejected", user.user_id)
            raise IdentityMismatchError(user.user_id)
        return

    if user.keycloak_sub is None:
        # Atomic first claim: of two concurrent first logins only one pins.
        claimed = await db.execute(
            update(CustomUser)
            .where(CustomUser.user_id == user.user_id, CustomUser.keycloak_sub.is_(None))
            .values(keycloak_sub=token.sub)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount:
            set_committed_value(user, "keycloak_sub", token.sub)
            logger.info("Pinned user %s to Keycloak sub %s", user.user_id, token.sub)
            return
        await db.refresh(user, ["keycloak_sub"])

    if user.keycloak_sub != token.sub:
        logger.error(
            "Keycloak sub mismatch for user %s: pinned=%s token=%s (azp=%s)%s",
            user.user_id,
            user.keycloak_sub,
            token.sub,
            token.azp,
            "; rejected" if enforce else "",
        )
        if enforce:
            raise IdentityMismatchError(user.user_id)


async def resolve_default_teams(db: AsyncSession, user_id: str) -> list[str]:
    """Resolve default teams: base team (all users) + prefix-matched teams."""
    team_ids: list[str] = []

    # 1. Base team — assigned to ALL new users
    base_result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'default_team_id'")
    )
    base_team = base_result.scalar()
    if base_team:
        team_ids.append(base_team)

    # 2. Prefix rules — additional teams based on user ID prefix
    rules_result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'default_team_rules'")
    )
    rules_raw = rules_result.scalar()
    if rules_raw:
        rules: list[dict] = json.loads(rules_raw)
        upper_id = user_id.upper()
        for rule in rules:
            if upper_id.startswith(rule.get("prefix", "").upper()):
                for t in rule.get("teams", []):
                    if t not in team_ids:
                        team_ids.append(t)
                break  # first matching rule only

    # Fallback to default_team_id (supports comma-separated multiple IDs)
    fallback_result = await db.execute(
        text("SELECT value FROM custom_portal_settings WHERE key = 'default_team_id'")
    )
    fallback = fallback_result.scalar()
    if fallback:
        return [t.strip() for t in fallback.split(",") if t.strip()]
    return []


async def auto_provision_user(db: AsyncSession, litellm_db: AsyncSession, user_id: str, email: str) -> None:
    """Create user in LiteLLM_UserTable and add to default teams if not exists."""
    # Check if user already exists
    result = await litellm_db.execute(
        text('SELECT user_id FROM "LiteLLM_UserTable" WHERE user_id = :user_id'),
        {"user_id": user_id},
    )
    if result.scalar_one_or_none() is not None:
        return  # Already exists

    # Resolve teams for this user
    team_ids = await resolve_default_teams(db, user_id)

    # Create user
    await litellm_db.execute(
        text(
            'INSERT INTO "LiteLLM_UserTable" (user_id, user_email, teams, spend, max_budget) '
            "VALUES (:user_id, :email, :teams, 0, NULL) "
            "ON CONFLICT (user_id) DO NOTHING"
        ),
        {"user_id": user_id, "email": email, "teams": team_ids},
    )
    logger.info("Auto-provisioned user: %s", user_id)

    # Add to each team
    for team_id in team_ids:
        await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamTable" '
                "SET members = array_append(members, :user_id) "
                "WHERE team_id = :team_id AND NOT (:user_id = ANY(COALESCE(members, ARRAY[]::text[])))"
            ),
            {"user_id": user_id, "team_id": team_id},
        )
        await litellm_db.execute(
            text(
                'INSERT INTO "LiteLLM_TeamMembership" (user_id, team_id, spend) '
                "VALUES (:user_id, :team_id, 0) "
                "ON CONFLICT DO NOTHING"
            ),
            {"user_id": user_id, "team_id": team_id},
        )
        logger.info("Added user %s to team %s", user_id, team_id)

    await litellm_db.commit()


