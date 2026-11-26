"""Worker process for background jobs (auto-deprecation, membership expiry, etc.)."""

import asyncio
import logging

from app.config import settings
from app.jobs.apply_cost_schedule import cost_schedule_loop
from app.jobs.auto_deprecate import deprecation_loop
from app.jobs.expire_budget_boosts import budget_boost_loop
from app.jobs.expire_memberships import membership_expiry_loop
from app.jobs.observe_kv_capacity import observe_loop as kv_observation_loop
from app.jobs.reconcile_benchmarks import reconcile_loop as benchmark_reconcile_loop
from app.jobs.reconcile_deployments import reconcile_loop
from app.jobs.reconcile_recipe_validations import reconcile_loop as recipe_validation_loop
from app.jobs.reset_team_membership_budget import team_membership_budget_reset_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Starting background worker...")
    await asyncio.gather(
        deprecation_loop(interval_seconds=300),
        budget_boost_loop(interval_seconds=300),
        membership_expiry_loop(interval_seconds=3600),
        team_membership_budget_reset_loop(interval_seconds=3600),
        cost_schedule_loop(interval_seconds=settings.cost_schedule_interval_seconds),
        reconcile_loop(interval_seconds=60),
        kv_observation_loop(),
        benchmark_reconcile_loop(interval_seconds=30),
        recipe_validation_loop(interval_seconds=10),
    )


if __name__ == "__main__":
    asyncio.run(main())
