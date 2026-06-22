"""Cron job that applies time-of-day cost rules to LiteLLM model deployments.

For every model that has a configured cost catalog entry:
1. Pick the highest-priority enabled rule whose UTC time window covers now.
2. If no rule matches, fall back to the catalog's default_*_cost_per_token.
3. Push the resulting cost to every LiteLLM deployment for that model_name via
   /model/update, but only when the values actually differ (to avoid pointless
   writes and proxy reloads).
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.clients.litellm import LiteLLMClient
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.models.custom_model_cost_schedule import CustomModelCostSchedule
from app.db.session import async_session_factory

logger = logging.getLogger(__name__)


def _rule_matches(rule: CustomModelCostSchedule, now: datetime) -> bool:
    """True when `now` falls inside this rule's UTC window for the right weekday.

    Day-spanning rules (hour_end_utc <= hour_start_utc) are interpreted as:
      - active from `hour_start_utc` on a day in `days_of_week`
      - through `hour_end_utc` of the following day
    """
    days = set(rule.days_of_week or [])
    if not days:
        return False

    current_dow = now.isoweekday()  # 1=Mon..7=Sun
    yesterday_dow = ((current_dow - 1 - 1) % 7) + 1
    hour = now.hour
    start = rule.hour_start_utc
    end = rule.hour_end_utc

    if end > start:
        return current_dow in days and start <= hour < end

    # Day-spanning (e.g. start=22, end=6 → Mon 22:00 to Tue 06:00 for days={Mon})
    if current_dow in days and hour >= start:
        return True
    if yesterday_dow in days and hour < end:
        return True
    return False


def _pick_rule(rules: list[CustomModelCostSchedule], now: datetime) -> CustomModelCostSchedule | None:
    """Highest-priority enabled rule whose window covers `now`."""
    active = [r for r in rules if r.enabled and _rule_matches(r, now)]
    if not active:
        return None
    active.sort(key=lambda r: r.priority, reverse=True)
    return active[0]


async def apply_cost_schedule() -> dict:
    """Run one pass: evaluate all rules and push deltas to LiteLLM."""
    now = datetime.now(UTC)
    catalogs_processed = 0
    deployments_updated = 0
    errors = 0
    skipped_config: set[str] = set()

    litellm = LiteLLMClient()

    async with async_session_factory() as portal_db:
        rule_result = await portal_db.execute(select(CustomModelCostSchedule))
        rules_all = rule_result.scalars().all()

        catalog_result = await portal_db.execute(
            select(CustomModelCatalog).where(
                (CustomModelCatalog.default_input_cost_per_token.isnot(None))
                | (CustomModelCatalog.default_output_cost_per_token.isnot(None))
            )
        )
        catalogs = catalog_result.scalars().all()

    rules_by_model: dict[str, list[CustomModelCostSchedule]] = {}
    for r in rules_all:
        rules_by_model.setdefault(r.model_name, []).append(r)
    catalog_by_model: dict[str, CustomModelCatalog] = {c.model_name: c for c in catalogs}

    # Process any model with rules OR a default-cost catalog row.
    target_models = set(rules_by_model) | set(catalog_by_model)
    if not target_models:
        return {"catalogs_processed": 0, "deployments_updated": 0, "errors": 0}

    try:
        litellm_models = await litellm.get_model_info()
    except Exception:
        logger.exception("Failed to fetch LiteLLM model_info for cost schedule")
        return {"catalogs_processed": 0, "deployments_updated": 0, "errors": 1}

    deployments_by_name: dict[str, list[dict]] = {}
    for lm in litellm_models:
        name = lm.get("model_name")
        if name:
            deployments_by_name.setdefault(name, []).append(lm)

    for model_name in target_models:
        catalogs_processed += 1
        rules = rules_by_model.get(model_name, [])
        catalog = catalog_by_model.get(model_name)
        active_rule = _pick_rule(rules, now)
        if active_rule is not None:
            target_in = active_rule.input_cost_per_token
            target_out = active_rule.output_cost_per_token
            source = f"rule:{active_rule.id}"
        elif catalog is not None:
            target_in = catalog.default_input_cost_per_token
            target_out = catalog.default_output_cost_per_token
            source = "default"
        else:
            # Rule expired and no catalog default — skip; admin must register
            # a default to control reverted price.
            continue

        deployments = deployments_by_name.get(model_name, [])
        if not deployments:
            continue

        for deployment in deployments:
            info = deployment.get("model_info", {}) or {}
            params = deployment.get("litellm_params", {}) or {}
            mid = info.get("id")
            if not mid:
                continue
            # LiteLLM rejects /model/update for config-defined models with a 400
            # ("Can't edit model. Model in config."). Skip them — both apply and
            # revert are impossible via the API until the model is stored in the DB.
            if info.get("db_model") is False:
                skipped_config.add(model_name)
                continue
            current_in = params.get("input_cost_per_token") or info.get("input_cost_per_token")
            current_out = params.get("output_cost_per_token") or info.get("output_cost_per_token")
            if current_in == target_in and current_out == target_out:
                continue
            try:
                await litellm.update_model(
                    mid,
                    litellm_params={
                        "input_cost_per_token": target_in,
                        "output_cost_per_token": target_out,
                    },
                )
                deployments_updated += 1
                logger.info(
                    "Cost schedule: %s id=%s in=%s out=%s (source=%s)",
                    model_name,
                    mid,
                    target_in,
                    target_out,
                    source,
                )
            except Exception:
                errors += 1
                logger.exception(
                    "Failed to update LiteLLM model %s (id=%s)",
                    model_name,
                    mid,
                )

    if skipped_config:
        logger.warning(
            "Cost schedule: %d model(s) are config-defined in LiteLLM and cannot "
            "be updated via the API (time-of-day pricing has no effect): %s. "
            "Register them in the DB (deploy via the portal / /model/new) to enable.",
            len(skipped_config),
            ", ".join(sorted(skipped_config)),
        )

    return {
        "catalogs_processed": catalogs_processed,
        "deployments_updated": deployments_updated,
        "errors": errors,
        "skipped_config": len(skipped_config),
    }


async def cost_schedule_loop(interval_seconds: int = 300) -> None:
    """Run the cost schedule evaluator on a fixed cadence."""
    logger.info("Starting cost schedule worker (interval=%ds)", interval_seconds)
    while True:
        try:
            result = await apply_cost_schedule()
            if result["deployments_updated"] > 0 or result["errors"] > 0:
                logger.info(
                    "Cost schedule pass: %d catalogs, %d updates, %d errors",
                    result["catalogs_processed"],
                    result["deployments_updated"],
                    result["errors"],
                )
        except Exception:
            logger.exception("Error in cost schedule loop")
        await asyncio.sleep(interval_seconds)
