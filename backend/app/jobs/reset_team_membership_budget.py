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


CHUNK_SIZE = 5000  # rows per UPDATE batch
CHUNK_SLEEP_SECONDS = 0.1  # breather between chunks so other writers can progress


async def _reset_one_budget(litellm_db, budget_id: str, now: datetime, delta) -> int:
    """Chunk-update memberships for a single budget and roll its reset_at forward.

    Uses ctid-based pagination so each UPDATE touches at most CHUNK_SIZE rows,
    committing between chunks to release row locks. Returns total rows reset.
    """
    total = 0
    while True:
        chunk_result = await litellm_db.execute(
            text(
                'UPDATE "LiteLLM_TeamMembership" SET spend = 0 '
                "WHERE ctid IN ("
                '  SELECT ctid FROM "LiteLLM_TeamMembership" '
                "  WHERE budget_id = :budget_id AND spend <> 0 "
                "  LIMIT :limit"
                ")"
            ),
            {"budget_id": budget_id, "limit": CHUNK_SIZE},
        )
        affected = chunk_result.rowcount or 0
        await litellm_db.commit()  # release locks immediately
        total += affected
        if affected < CHUNK_SIZE:
            break
        # let other writers in before the next pass
        await asyncio.sleep(CHUNK_SLEEP_SECONDS)

    await litellm_db.execute(
        text(
            'UPDATE "LiteLLM_BudgetTable" '
            "SET budget_reset_at = :next_reset, updated_at = :now "
            "WHERE budget_id = :budget_id"
        ),
        {"next_reset": now + delta, "now": now, "budget_id": budget_id},
    )
    await litellm_db.commit()
    return total


async def reset_team_membership_budgets() -> dict:
    """Reset spend on TeamMembership rows whose budget reset time has passed.

    Each budget is processed in its own short-lived transaction; UPDATEs are
    chunked to CHUNK_SIZE rows to avoid long lock holds on the membership table.
    """
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

                count = await _reset_one_budget(litellm_db, budget_id, now, delta)
                memberships_reset += count
                processed += 1
                logger.info(
                    "Reset budget %s (%d memberships), next reset at %s",
                    budget_id,
                    count,
                    (now + delta).isoformat(),
                )
            except Exception:
                errors += 1
                logger.error("Failed to reset budget %s", budget_id, exc_info=True)
                # transaction state may be aborted from the failure; ensure rollback
                try:
                    await litellm_db.rollback()
                except Exception:
                    logger.exception("Rollback failed after reset error")

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
