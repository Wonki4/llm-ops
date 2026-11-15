"""Publish time windows to LiteLLM, which selects the rate when billing a request."""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import groupby
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from app.clients.litellm import BASE_PRICE_FIELDS, LiteLLMClient, deployment_base_prices
from app.config import settings
from app.db.models.custom_model_cost_schedule import CustomAppliedCostSchedule, CustomModelCostSchedule
from app.db.session import async_session_factory, litellm_session_factory

logger = logging.getLogger(__name__)


def schedule_tz() -> ZoneInfo:
    """The configured schedule timezone, falling back to UTC if unknown."""
    name = settings.schedule_timezone or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown schedule_timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC")


def _rule_matches(rule: CustomModelCostSchedule, local: datetime) -> bool:
    day, hour = local.isoweekday(), local.hour
    start, end = rule.hour_start_local, rule.hour_end_local
    if start < end:
        return day in rule.days_of_week and start <= hour < end
    # After midnight an overnight rule still belongs to the previous weekday.
    yesterday = (day - 2) % 7 + 1
    return (day in rule.days_of_week and hour >= start) or (yesterday in rule.days_of_week and hour < end)


async def _current_revisions(ids: list[str]) -> dict[str, datetime]:
    # /model/info omits updated_at on the open-source proxy. Only read the two
    # columns needed to distinguish a later direct write, including an equal price.
    async with litellm_session_factory() as db:
        result = await db.execute(
            text('SELECT model_id, updated_at FROM "LiteLLM_ProxyModelTable" WHERE model_id = ANY(:ids)'),
            {"ids": ids},
        )
        return {
            mid: stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC) for mid, stamp in result
        }


# LiteLLM shallow-merges the nested cost-map entry in memory. Explicitly
# replace both window forms and clear optional rates left by a previous rule.
EMPTY_SCHEDULE = {
    "hours_utc": [],
    "windows": [],
    "weekday_timezone": "UTC",
    **dict.fromkeys((*BASE_PRICE_FIELDS, "output_cost_per_reasoning_token", "cache_creation_input_token_cost")),
}
PREVIOUS_SCHEDULE = "_litellm_ops_previous_schedule"


def _week_slots(now: datetime, zone: ZoneInfo) -> Iterator[tuple[datetime, datetime, datetime]]:
    """UTC segments split at midnight and every change of the local wall-clock hour."""
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    for day in range(7):
        midnight = start + timedelta(days=day)
        end = midnight + timedelta(days=1)
        cursor = midnight
        while cursor < end:
            local = cursor.astimezone(zone)
            following = min(end, cursor + timedelta(minutes=60 - local.minute))
            yield cursor, following, local
            cursor = following


def _native_schedule(
    rules: list[CustomModelCostSchedule], slots: Iterable[tuple[datetime, datetime, datetime]], now: datetime
) -> tuple[dict | None, bool]:
    """LiteLLM accepts one rate per schedule. Publish every window with the current or next winning rate."""

    def window_key(slot):
        start, _, local = slot
        rule = next((r for r in rules if _rule_matches(r, local)), None)
        if rule is None:
            return start.isoweekday(), None
        return start.isoweekday(), {
            "input_cost_per_token": rule.input_cost_per_token,
            "output_cost_per_token": rule.output_cost_per_token,
            "cache_read_input_token_cost": rule.cache_read_cost_per_token,
        }

    periods = []
    for (_, rates), adjacent in groupby(slots, key=window_key):
        if rates is not None:
            span = list(adjacent)
            periods.append((span[0][0], span[-1][1], rates))
    if not periods:
        return None, False

    start, end, selected = next((period for period in periods if period[1] > now), periods[0])
    windows = [
        {"weekdays": [begin.isoweekday()], "hours_utc": f"{begin:%H:%M}-{finish:%H:%M}"}
        for begin, finish, rates in sorted(periods, key=lambda period: period[0].isoweekday())
        if rates == selected
    ]
    return {**EMPTY_SCHEDULE, **selected, "windows": windows}, start <= now < end


def _desired_schedule(current: dict, schedule: dict | None, active: bool) -> dict:
    owned = PREVIOUS_SCHEDULE in current
    original = current.get(PREVIOUS_SCHEDULE, current)
    # Preserve provider-configured off-peak pricing outside portal rule hours.
    if schedule and (active or not (original.get("hours_utc") or original.get("windows"))):
        return {**schedule, PREVIOUS_SCHEDULE: original if original != EMPTY_SCHEDULE else {}}
    if owned:
        return {**EMPTY_SCHEDULE, **original}
    return current


async def _legacy_restore(receipt: CustomAppliedCostSchedule, model: dict) -> dict:
    """The #275 worker handoff is the only path that may write base prices."""
    mid = receipt.deployment_id
    revision = (await _current_revisions([mid])).get(mid)
    if revision is None:
        raise RuntimeError("Cannot verify the legacy price against the LiteLLM database")
    live = deployment_base_prices(model)
    if (
        receipt.model_name == model["model_name"]
        and receipt.litellm_updated_at == revision
        and all(live.get(key) == value for key, value in receipt.applied_rates.items())
    ):
        return {key: value for key, value in receipt.previous_rates.items() if key in BASE_PRICE_FIELDS}
    return {}


async def apply_cost_schedule() -> dict:
    stats = dict(catalogs_processed=0, deployments_updated=0, errors=0, skipped_config=0)
    async with async_session_factory.begin() as db:
        if not await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended('portal.apply_cost_schedule', 0))")
        ):
            return stats
        now = datetime.now(UTC)
        slots = list(_week_slots(now, schedule_tz()))
        rules = await db.scalars(
            select(CustomModelCostSchedule)
            .where(CustomModelCostSchedule.enabled.is_(True))
            .order_by(
                CustomModelCostSchedule.priority.desc(), CustomModelCostSchedule.created_at, CustomModelCostSchedule.id
            )
        )
        by_model = defaultdict(list)
        for rule in rules:
            by_model[rule.model_name].append(rule)
        schedules = {name: _native_schedule(group, slots, now) for name, group in by_model.items()}
        stats["catalogs_processed"] = len(schedules)
        receipts = {r.deployment_id: r for r in await db.scalars(select(CustomAppliedCostSchedule))}

        proxy = LiteLLMClient()
        try:
            models = await proxy.get_model_info()
            if (
                not schedules
                and not receipts
                and not any(
                    PREVIOUS_SCHEDULE in ((model.get("model_info") or {}).get("off_peak_pricing") or {})
                    for model in models
                )
            ):
                return stats
            await proxy.check_cost_schedule_support()
        except Exception:
            logger.exception("Cannot synchronize LiteLLM time-of-day pricing")
            stats["errors"] += 1
            return stats

        for model in models:
            info = model.get("model_info") or {}
            mid = info.get("id")
            receipt = receipts.pop(mid, None)
            if not mid:
                continue
            try:
                current = info.get("off_peak_pricing") or {}
                schedule, active = schedules.get(model.get("model_name"), (None, False))
                desired = _desired_schedule(current, schedule, active)
                if desired == current and receipt is None:
                    continue
                if info.get("db_model") is False:
                    stats["skipped_config"] += 1
                    continue
                restore = await _legacy_restore(receipt, model) if receipt else {}
                if desired != current or restore:
                    await proxy.set_cost_schedule(mid, desired, legacy_restore=restore or None)
                    stats["deployments_updated"] += 1
                if receipt:
                    await db.delete(receipt)
            except Exception:
                logger.exception("Failed to sync time-of-day pricing for %s", mid)
                stats["errors"] += 1

        if receipts:
            # Some IDs may be hidden from the API. Only retire receipts for confirmed deletions.
            try:
                existing = await _current_revisions(list(receipts))
                for mid in receipts.keys() - existing.keys():
                    await db.delete(receipts[mid])
            except Exception:
                logger.exception("Failed to check deleted deployments with legacy price receipts")
                stats["errors"] += 1
    return stats


async def cost_schedule_loop(interval_seconds: int = 300) -> None:
    """Align passes to the clock so adjacent hourly rates switch at the boundary."""
    logger.info("Starting cost schedule worker (interval=%ds, boundary-aligned)", interval_seconds)
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
        # Sleep to the next interval boundary so passes land at predictable
        # wall-clock times (:00 of each minute for 60s), close to rule edges.
        await asyncio.sleep(interval_seconds - (time.time() % interval_seconds))
