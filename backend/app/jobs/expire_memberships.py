"""Cron job to expire team memberships and remove members + their keys."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory, litellm_session_factory

logger = logging.getLogger(__name__)


async def expire_memberships() -> dict:
    """Find expired memberships and remove members from teams + delete their keys."""
    now = datetime.now()
    processed = 0
    errors = 0

    async with async_session_factory() as db:
        # Find active memberships that have expired
        result = await db.execute(
            text(
                "SELECT id, user_id, team_id FROM custom_team_membership "
                "WHERE status = 'active' AND expires_at <= :now"
            ),
            {"now": now},
        )
        expired = result.mappings().all()

        if not expired:
            return {"processed": 0, "errors": 0}

        logger.info("Found %d expired memberships to process", len(expired))

        async with litellm_session_factory() as litellm_db:
            for row in expired:
                user_id = row["user_id"]
                team_id = row["team_id"]
                membership_id = row["id"]

                try:
                    # 1. Delete user's keys for this team
                    key_result = await litellm_db.execute(
                        text(
                            'DELETE FROM "LiteLLM_VerificationToken" '
                            "WHERE user_id = :user_id AND team_id = :team_id "
                            "RETURNING token"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )
                    deleted_keys = key_result.rowcount
                    logger.info("Deleted %d keys for user %s in team %s", deleted_keys, user_id, team_id)

                    # 2. Remove from TeamMembership
                    await litellm_db.execute(
                        text(
                            'DELETE FROM "LiteLLM_TeamMembership" '
                            "WHERE user_id = :user_id AND team_id = :team_id"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )

                    # 3. Remove from TeamTable.members array
                    await litellm_db.execute(
                        text(
                            'UPDATE "LiteLLM_TeamTable" '
                            "SET members = array_remove(members, :user_id) "
                            "WHERE team_id = :team_id"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )

                    # 4. Remove from members_with_roles JSONB
                    await litellm_db.execute(
                        text(
                            'UPDATE "LiteLLM_TeamTable" SET members_with_roles = ('
                            "SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb) "
                            "FROM jsonb_array_elements(members_with_roles) elem "
                            "WHERE elem->>'user_id' != :user_id"
                            ") WHERE team_id = :team_id"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )

                    # 5. Remove from admins array (if admin)
                    await litellm_db.execute(
                        text(
                            'UPDATE "LiteLLM_TeamTable" '
                            "SET admins = array_remove(admins, :user_id) "
                            "WHERE team_id = :team_id"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )

                    # 6. Remove team from UserTable.teams array
                    await litellm_db.execute(
                        text(
                            'UPDATE "LiteLLM_UserTable" '
                            "SET teams = array_remove(teams, :team_id) "
                            "WHERE user_id = :user_id"
                        ),
                        {"user_id": user_id, "team_id": team_id},
                    )

                    # 7. Mark membership as expired
                    await db.execute(
                        text(
                            "UPDATE custom_team_membership SET status = 'expired' WHERE id = :id"
                        ),
                        {"id": membership_id},
                    )

                    processed += 1
                    logger.info("Expired membership: user=%s team=%s", user_id, team_id)

                except Exception:
                    errors += 1
                    logger.error("Failed to expire membership: user=%s team=%s", user_id, team_id, exc_info=True)

            await litellm_db.commit()
        await db.commit()

    return {"processed": processed, "errors": errors}


async def membership_expiry_loop(interval_seconds: int = 3600) -> None:
    """Run the membership expiry check in a loop."""
    logger.info("Starting membership expiry worker (interval=%ds)", interval_seconds)
    while True:
        try:
            result = await expire_memberships()
            if result["processed"] > 0:
                logger.info("Expired %d membership(s), %d error(s)", result["processed"], result["errors"])
        except Exception:
            logger.exception("Error in membership expiry loop")
        await asyncio.sleep(interval_seconds)
