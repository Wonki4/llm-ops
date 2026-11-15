"""Apply time-of-day prices and restore confirmed worker writes per LiteLLM deployment."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select, text
from sqlalchemy.exc import OperationalError

from app.clients.litellm import LiteLLMClient
from app.config import settings
from app.db.models.custom_model_catalog import CustomModelCatalog
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


def _rule_matches(rule: CustomModelCostSchedule, now: datetime) -> bool:
    """True when local `now` falls inside this rule's window for the right weekday.

    `now` must already be in the schedule timezone. Day-spanning rules
    (hour_end_local <= hour_start_local) are interpreted as:
      - active from `hour_start_local` on a day in `days_of_week`
      - through `hour_end_local` of the following day
    """
    days = set(rule.days_of_week or [])
    if not days:
        return False

    current_dow = now.isoweekday()  # 1=Mon..7=Sun
    yesterday_dow = ((current_dow - 1 - 1) % 7) + 1
    hour = now.hour
    start = rule.hour_start_local
    end = rule.hour_end_local

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


PRICE_FIELDS = {
    "input_cost_per_token": "input_cost_per_token",
    "output_cost_per_token": "output_cost_per_token",
    "cache_read_input_token_cost": "cache_read_cost_per_token",
}


def _utc_revision(value: str | datetime) -> datetime:
    revision = datetime.fromisoformat(value) if isinstance(value, str) else value
    # Prisma stores its PostgreSQL timestamps in UTC without a timezone.
    return revision.replace(tzinfo=UTC) if revision.tzinfo is None else revision.astimezone(UTC)


async def _current_revisions(ids: list[str]) -> dict[str, datetime]:
    if not ids:
        return {}
    # /model/info omits updated_at on the open-source proxy. Only read the two
    # columns needed to distinguish a later direct write, including an equal price.
    async with litellm_session_factory() as db:
        result = await db.execute(
            text('SELECT model_id, updated_at FROM "LiteLLM_ProxyModelTable" WHERE model_id = ANY(:ids)'),
            {"ids": ids},
        )
        return {mid: _utc_revision(stamp) for mid, stamp in result}


def _live_rates(deployment: dict) -> dict[str, float | None]:
    params = deployment.get("litellm_params") or {}
    info = deployment.get("model_info") or {}
    return {key: params[key] if params.get(key) is not None else info.get(key) for key in PRICE_FIELDS}


async def _save_receipt(receipt: CustomAppliedCostSchedule) -> None:
    # A transient portal DB failure must retry the receipt, never the LiteLLM write.
    for attempt in range(2):
        try:
            async with async_session_factory() as db:
                await db.merge(receipt)
                await db.commit()
            return
        except OperationalError:
            if attempt:
                raise


async def _reconcile_deployment(
    litellm: LiteLLMClient,
    model_name: str,
    deployment: dict,
    catalog: CustomModelCatalog | None,
    rule: CustomModelCostSchedule | None,
    receipt: CustomAppliedCostSchedule | None,
    revision: datetime | None,
) -> tuple[dict[str, float | None], bool]:
    """Return effective rates and whether a proxy write occurred."""
    mid = deployment["model_info"]["id"]
    live = _live_rates(deployment)
    defaults = {key: getattr(catalog, f"default_{attr}", None) for key, attr in PRICE_FIELDS.items()}
    owned = receipt is not None and (
        receipt.model_name == model_name
        and receipt.litellm_updated_at == revision
        and all(live[key] == value for key, value in receipt.applied_rates.items())
    )
    previous = receipt.previous_rates if owned else {}
    # If the previous LiteLLM value was absent, a subsequently configured numeric
    # baseline can finish the restore. LiteLLM ignores null values on model/update.
    targets = {key: value if value is not None else defaults[key] for key, value in previous.items()}
    scheduled = {}
    if rule is not None:
        scheduled = {key: value for key, attr in PRICE_FIELDS.items() if (value := getattr(rule, attr)) is not None}
        targets.update(scheduled)
    elif catalog is not None and catalog.default_cost_managed:
        targets.update({key: value for key, value in defaults.items() if value is not None})

    changes = {key: value for key, value in targets.items() if value is not None and live[key] != value}
    if changes:
        if owned and rule is None and not (catalog and catalog.default_cost_managed):
            # Other deployments in this pass may have taken time. Recheck before issuing the restore.
            if (await _current_revisions([mid])).get(mid) != revision:
                raise RuntimeError("Deployment changed before price restoration; retry on the next pass")
        response = await litellm.update_model(mid, litellm_params=changes)
        # Keep the original baseline across successive rules. An unchanged field
        # without a previous write was never ours to restore.
        before = {key: None for key, value in targets.items() if value is None and key in previous}
        for key, rate in scheduled.items():
            if key in previous or key in changes:
                baseline = previous.get(key, live[key] if live[key] is not None else defaults[key])
                if baseline != rate:
                    before[key] = baseline
        live.update(changes)
        if before:
            await _save_receipt(
                CustomAppliedCostSchedule(
                    deployment_id=mid,
                    model_name=model_name,
                    litellm_updated_at=_utc_revision(response["updated_at"]),
                    previous_rates=before,
                    applied_rates={key: live[key] for key in before},
                )
            )
            return live, True
    elif owned and (rule is not None or any(value is None for value in targets.values())):
        return live, False

    if receipt is not None:
        async with async_session_factory() as db:
            await db.delete(receipt)
            await db.commit()
    return live, bool(changes)


async def apply_cost_schedule() -> dict:
    """Serialize passes so a second worker cannot act on a pre-write snapshot."""
    async with async_session_factory() as lock_db:
        acquired = await lock_db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended('portal.apply_cost_schedule', 0))")
        )
        if not acquired:
            return {"catalogs_processed": 0, "deployments_updated": 0, "errors": 0}
        return await _apply_cost_schedule()


async def _apply_cost_schedule() -> dict:
    now = datetime.now(UTC).astimezone(schedule_tz())
    stats = dict(
        catalogs_processed=0, deployments_updated=0, errors=0, skipped_config=0, missing_default=0, refreshed_defaults=0
    )
    async with async_session_factory() as db:
        rules = (await db.scalars(select(CustomModelCostSchedule))).all()
        receipts = (await db.scalars(select(CustomAppliedCostSchedule))).all()
        tracked_names = {r.model_name for r in (*rules, *receipts)}
        catalogs = (
            await db.scalars(
                select(CustomModelCatalog).where(
                    CustomModelCatalog.model_name.in_(tracked_names)
                    | CustomModelCatalog.default_input_cost_per_token.isnot(None)
                    | CustomModelCatalog.default_output_cost_per_token.isnot(None)
                )
            )
        ).all()

    rules_by_model: dict[str, list[CustomModelCostSchedule]] = {}
    for rule in rules:
        rules_by_model.setdefault(rule.model_name, []).append(rule)
    by_id = {r.deployment_id: r for r in receipts}
    by_name = {c.model_name: c for c in catalogs}
    targets = tracked_names | by_name.keys()
    if not targets:
        return stats

    active_rules = {name: _pick_rule(rules_by_model.get(name, []), now) for name in targets}
    litellm = LiteLLMClient()
    try:
        models = await litellm.get_model_info()
    except Exception:
        logger.exception("Failed to read LiteLLM deployment prices")
        stats["errors"] += 1
        return stats

    grouped: dict[str, list[dict]] = {}
    tracked_ids = set(by_id)
    for model in models:
        name = model.get("model_name")
        if name not in targets:
            continue
        grouped.setdefault(name, []).append(model)
        info = model.get("model_info") or {}
        # Verify access to the same proxy database before the first scheduled write.
        if active_rules[name] and info.get("id") and info.get("db_model") is not False:
            tracked_ids.add(info["id"])
    try:
        revisions = await _current_revisions(list(tracked_ids))
    except Exception:
        logger.exception("Cannot verify price writes against the configured LiteLLM database")
        stats["errors"] += 1
        return stats

    for name in targets:
        stats["catalogs_processed"] += 1
        catalog = by_name.get(name)
        rule = active_rules[name]
        observed = set()
        failed = False
        skipped_config = False
        missing_default = False
        for deployment in grouped.get(name, []):
            info = deployment.get("model_info") or {}
            mid = info.get("id")
            if not mid:
                continue
            if info.get("db_model") is False:
                skipped_config = True
                continue
            receipt = by_id.get(mid)
            try:
                if mid in tracked_ids and mid not in revisions:
                    raise RuntimeError("Proxy deployment is missing from the configured LiteLLM database")
                if receipt and receipt.litellm_updated_at == revisions.get(mid):
                    missing_default |= any(
                        value is None and getattr(catalog, f"default_{PRICE_FIELDS[key]}", None) is None
                        for key, value in receipt.previous_rates.items()
                    )
                live, changed = await _reconcile_deployment(
                    litellm, name, deployment, catalog, rule, receipt, revisions.get(mid)
                )
                stats["deployments_updated"] += int(changed)
                observed.add(tuple(live.values()))
            except Exception:
                failed = True
                stats["errors"] += 1
                logger.exception("Failed to reconcile price for %s (id=%s)", name, mid)

        stats["skipped_config"] += int(skipped_config)
        stats["missing_default"] += int(missing_default)
        if missing_default:
            logger.warning("No numeric baseline to restore for one or more scheduled prices on %s", name)
        # A shared catalog row cannot represent conflicting per-deployment prices.
        if (
            failed
            or missing_default
            or rule is not None
            or not catalog
            or catalog.default_cost_managed
            or len(observed) != 1
        ):
            continue
        din, dout, dcr = observed.pop()
        if din is None or dout is None:
            continue
        stored = (
            catalog.default_input_cost_per_token,
            catalog.default_output_cost_per_token,
            catalog.default_cache_read_cost_per_token,
        )
        if (din, dout, dcr if dcr is not None else stored[2]) == stored:
            continue
        async with async_session_factory() as db:
            result = await db.execute(
                text(
                    "UPDATE custom_model_catalog SET default_input_cost_per_token = :i, "
                    "default_output_cost_per_token = :o, "
                    "default_cache_read_cost_per_token = COALESCE(:cr, default_cache_read_cost_per_token) "
                    "WHERE model_name = :m AND NOT default_cost_managed"
                ),
                {"i": din, "o": dout, "cr": dcr, "m": name},
            )
            await db.commit()
            stats["refreshed_defaults"] += result.rowcount

    # A deleted deployment has no price to restore. Never infer deletion from a filtered proxy listing.
    listed_ids = {(m.get("model_info") or {}).get("id") for m in models}
    removed = by_id.keys() - revisions.keys() - listed_ids
    if removed:
        async with async_session_factory() as db:
            await db.execute(
                delete(CustomAppliedCostSchedule).where(CustomAppliedCostSchedule.deployment_id.in_(removed))
            )
            await db.commit()
    return stats


async def cost_schedule_loop(interval_seconds: int = 300) -> None:
    """Run the cost schedule evaluator on a fixed cadence.

    Each pass is aligned to a wall-clock multiple of the interval (e.g. every
    :00/:05/.../:55 for the 300s default). Rules are hour-granular, so an
    on-the-hour transition like 04:00 is caught by the 04:00 pass and applied
    within seconds of the boundary — not up to a full interval late.
    """
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
