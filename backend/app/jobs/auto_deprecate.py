"""Background job: auto-deprecate models when their scheduled date arrives."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import DateTime, cast, select

from app.clients.litellm import LiteLLMClient
from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


async def run_auto_deprecation() -> int:
    """Check for models whose status_schedule['deprecated'] is past due and
    deprecate them. Returns the number of models deprecated.
    """
    count = 0
    now = datetime.now(UTC)
    litellm = LiteLLMClient()

    scheduled_deprecated_at = cast(
        CustomModelCatalog.status_schedule["deprecated"].astext,
        DateTime(timezone=True),
    )

    async with async_session_factory() as session:
        result = await session.execute(
            select(CustomModelCatalog).where(
                CustomModelCatalog.status != ModelStatus.DEPRECATED,
                scheduled_deprecated_at.isnot(None),
                scheduled_deprecated_at <= now,
            )
        )
        models_to_deprecate = result.scalars().all()

        for model in models_to_deprecate:
            try:
                # Try to remove from LiteLLM
                litellm_models = await litellm.get_model_info()
                for lm in litellm_models:
                    if lm.get("model_name") == model.model_name:
                        model_id = lm.get("model_info", {}).get("id")
                        if model_id:
                            await litellm.delete_model(model_id)
                            logger.info("Removed model %s (id=%s) from LiteLLM", model.model_name, model_id)

                # Update catalog status
                model.status = ModelStatus.DEPRECATED
                model.status_change_date = now
                model.updated_by = "system:auto_deprecate"
                count += 1
                logger.info("Deprecated model: %s", model.model_name)
            except Exception:
                logger.exception("Failed to deprecate model: %s", model.model_name)

        await session.commit()

    return count


async def deprecation_loop(interval_seconds: int = 300) -> None:
    """Run the deprecation check in a loop."""
    logger.info("Starting auto-deprecation worker (interval=%ds)", interval_seconds)
    while True:
        try:
            count = await run_auto_deprecation()
            if count > 0:
                logger.info("Auto-deprecated %d model(s)", count)
        except Exception:
            logger.exception("Error in auto-deprecation loop")
        await asyncio.sleep(interval_seconds)
