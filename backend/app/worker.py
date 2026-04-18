"""Worker process for background jobs (auto-deprecation, membership expiry, etc.)."""

import asyncio
import logging

from app.jobs.auto_deprecate import deprecation_loop
from app.jobs.expire_memberships import membership_expiry_loop
from app.jobs.reset_team_membership_budget import team_membership_budget_reset_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Starting background worker...")
    await asyncio.gather(
        deprecation_loop(interval_seconds=300),
        membership_expiry_loop(interval_seconds=3600),
        team_membership_budget_reset_loop(interval_seconds=3600),
    )


if __name__ == "__main__":
    asyncio.run(main())
