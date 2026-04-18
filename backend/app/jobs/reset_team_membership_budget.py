"""Cron job to reset TeamMembership spend when their linked budget expires.

LiteLLM's native ResetBudgetJob occasionally misses TeamMembership rows in our
deployment, so we run the equivalent reset on our side:

1. Find budgets with `budget_reset_at <= now` that have at least one linked
   LiteLLM_TeamMembership row.
2. Zero the `spend` on those memberships.
3. Roll `budget_reset_at` forward by `budget_duration`.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import text

from app.api.teams import _parse_duration
from app.db.session import litellm_session_factory

logger = logging.getLogger(__name__)


async def reset_team_membership_budgets() -> dict:
    """Reset spend on TeamMembership rows whose budget reset time has passed."""
    now = datetime.now()
    processed = 0
    errors = 0
    memberships_reset = 0

    async with litellm_session_factory() as litellm_db:
        result = await litellm_db.execute(
            text(
                "SELECT b.budget_id, b.budget_duration "
                'FROM "LiteLLM_BudgetTable" b '
                "WHERE b.budget_reset_at IS NOT NULL "
                "  AND b.budget_reset_at <= :now "
                "  AND b.budget_duration IS NOT NULL "
                "  AND EXISTS ("
                '    SELECT 1 FROM "LiteLLM_TeamMembership" tm '
                "    WHERE tm.budget_id = b.budget_id"
                "  )"
            ),
            {"now": now},
        )
        budgets = result.mappings().all()

        if not budgets:
            return {"processed": 0, "errors": 0, "memberships_reset": 0}

        logger.info("Found %d budget(s) linked to TeamMembership to reset", len(budgets))

        for row in budgets:
            budget_id = row["budget_id"]
            duration_str = row["budget_duration"]
            try:
                delta = _parse_duration(duration_str)
                if delta is None:
                    logger.warning(
                        "Skipping budget %s: unparseable duration %r",
                        budget_id,
                        duration_str,
                    )
                    continue

                reset_result = await litellm_db.execute(
                    text(
                        'UPDATE "LiteLLM_TeamMembership" SET spend = 0 '
                        "WHERE budget_id = :budget_id"
                    ),
                    {"budget_id": budget_id},
                )
                memberships_reset += reset_result.rowcount or 0

                await litellm_db.execute(
                    text(
                        'UPDATE "LiteLLM_BudgetTable" '
                        "SET budget_reset_at = :next_reset, updated_at = :now "
                        "WHERE budget_id = :budget_id"
                    ),
                    {
                        "next_reset": now + delta,
                        "now": now,
                        "budget_id": budget_id,
                    },
                )

                processed += 1
                logger.info(
                    "Reset budget %s (%d memberships), next reset at %s",
                    budget_id,
                    reset_result.rowcount or 0,
                    (now + delta).isoformat(),
                )
            except Exception:
                errors += 1
                logger.error("Failed to reset budget %s", budget_id, exc_info=True)

        await litellm_db.commit()

    return {
        "processed": processed,
        "errors": errors,
        "memberships_reset": memberships_reset,
    }


async def team_membership_budget_reset_loop(interval_seconds: int = 3600) -> None:
    """Run TeamMembership budget reset in a loop."""
    logger.info(
        "Starting TeamMembership budget reset worker (interval=%ds)",
        interval_seconds,
    )
    while True:
        try:
            result = await reset_team_membership_budgets()
            if result["processed"] > 0:
                logger.info(
                    "Reset %d budget(s), %d membership(s), %d error(s)",
                    result["processed"],
                    result["memberships_reset"],
                    result["errors"],
                )
        except Exception:
            logger.exception("Error in TeamMembership budget reset loop")
        await asyncio.sleep(interval_seconds)
