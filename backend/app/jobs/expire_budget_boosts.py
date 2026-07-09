"""Cron job: revert member budget boosts whose window has ended.

Selects active boosts with expires_at <= now and restores each member's
snapshotted budget via LiteLLM. If the membership is gone, marks reverted
without an API call. On LiteLLM failure the boost stays active for the next
tick. Revert is unconditional — manual edits during the window are overwritten.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.clients.litellm import LiteLLMClient
from app.db.models.custom_member_budget_boost import CustomMemberBudgetBoost
from app.db.session import async_session_factory, litellm_session_factory

logger = logging.getLogger(__name__)


async def _membership_exists(litellm_db, team_id: str, user_id: str) -> bool:
    result = await litellm_db.execute(
        text(
            'SELECT 1 FROM "LiteLLM_TeamMembership" '
            "WHERE team_id = :team_id AND user_id = :user_id"
        ),
        {"team_id": team_id, "user_id": user_id},
    )
    return result.scalar() is not None


async def revert_expired_boosts(now: datetime) -> int:
    """Revert all active boosts with expires_at <= now. Returns count reverted."""
    reverted = 0
    async with async_session_factory() as db:
        expired = (
            await db.execute(
                select(CustomMemberBudgetBoost).where(
                    CustomMemberBudgetBoost.status == "active",
                    CustomMemberBudgetBoost.expires_at <= now,
                )
            )
        ).scalars().all()
        if not expired:
            return 0

        litellm = LiteLLMClient()
        async with litellm_session_factory() as litellm_db:
            for boost in expired:
                try:
                    if await _membership_exists(litellm_db, boost.team_id, boost.user_id):
                        await litellm.update_team_member(
                            boost.team_id,
                            boost.user_id,
                            max_budget_in_team=boost.original_max_budget,
                        )
                    boost.status = "reverted"
                    boost.reverted_at = datetime.now(UTC)
                    reverted += 1
                except Exception:  # noqa: BLE001 — leave active, retry next tick
                    logger.exception(
                        "Budget boost revert failed for %s/%s", boost.team_id, boost.user_id
                    )
        await db.commit()
    return reverted


async def budget_boost_loop(interval_seconds: int) -> None:
    logger.info("Starting budget-boost revert loop (interval=%ss)", interval_seconds)
    while True:
        try:
            n = await revert_expired_boosts(datetime.now(UTC))
            if n:
                logger.info("Reverted %s expired budget boost(s)", n)
        except Exception:  # noqa: BLE001 — never let the loop die
            logger.exception("Budget-boost revert tick failed")
        await asyncio.sleep(interval_seconds)
