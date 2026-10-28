"""User provisioning shared by every way a Keycloak identity enters the portal.

The browser login callback, the bearer/session dependency, and the inference
gateway's SSO token exchange all need the same two things: a portal user row
(``custom_users``, carrying the global role) and a LiteLLM user row placed in
the default teams.
"""

import json
import logging

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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


async def upsert_portal_user(db: AsyncSession, token: TokenPayload) -> CustomUser:
    """Return the portal user for ``token``, creating it and syncing the role."""
    uid = canonical_user_id(token)
    result = await db.execute(select(CustomUser).where(CustomUser.user_id == uid))
    user = result.scalar_one_or_none()
    is_super = _is_super(token)

    if not user:
        stmt = pg_insert(CustomUser).values(
            user_id=uid,
            email=token.email,
            display_name=token.name,
            global_role=GlobalRole.SUPER_USER if is_super else GlobalRole.USER,
        ).on_conflict_do_nothing(index_elements=["user_id"])
        await db.execute(stmt)
        await db.flush()

        # Re-fetch to get the ORM object
        result = await db.execute(select(CustomUser).where(CustomUser.user_id == uid))
        return result.scalar_one()

    new_role = GlobalRole.SUPER_USER if is_super else user.global_role
    if user.global_role != new_role:
        user.global_role = new_role
        await db.flush()
    return user


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


