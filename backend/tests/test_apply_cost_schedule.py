"""Regression tests for ownership of scheduled deployment prices."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.models.custom_model_cost_schedule import CustomAppliedCostSchedule, CustomModelCostSchedule
from app.jobs import apply_cost_schedule as job

INPUT = "input_cost_per_token"
OUTPUT = "output_cost_per_token"
CACHE = "cache_read_input_token_cost"


class PricingStore:
    def __init__(self):
        self.catalog = CustomModelCatalog(
            model_name="m", default_input_cost_per_token=3, default_output_cost_per_token=4
        )
        self.rule = CustomModelCostSchedule(
            model_name="m",
            days_of_week=list(range(1, 8)),
            hour_start_local=0,
            hour_end_local=24,
            priority=0,
            enabled=False,
            input_cost_per_token=8,
            output_cost_per_token=9,
        )
        self.rules = [self.rule]
        self.receipts = {}
        self.versions = {}
        self.models = {}
        self.clock = datetime(2026, 1, 1, tzinfo=UTC)
        self.locked = False
        self.fail_writes = set()
        self.proxy = SimpleNamespace(
            get_model_info=AsyncMock(side_effect=lambda: list(self.models.values())),
            update_model=AsyncMock(side_effect=self.write),
        )

    def deploy(self, mid, input_rate, output_rate, *, editable=True):
        self.models[mid] = {"model_name": "m", "model_info": {"id": mid, "db_model": editable}, "litellm_params": {}}
        self.direct(mid, {INPUT: input_rate, OUTPUT: output_rate})

    def direct(self, mid, rates):
        self.clock += timedelta(seconds=1)
        self.versions[mid] = self.clock
        self.models[mid]["litellm_params"].update(rates)

    async def write(self, mid, *, litellm_params):
        if mid in self.fail_writes:
            raise RuntimeError("proxy rejected update")
        self.direct(mid, litellm_params)
        return {"updated_at": self.clock.isoformat()}

    async def revisions(self, ids):
        return {mid: self.versions[mid] for mid in ids if mid in self.versions}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def scalar(self, stmt):
        assert "pg_try_advisory_xact_lock" in str(stmt)
        return not self.locked

    async def scalars(self, stmt):
        rows = {
            CustomAppliedCostSchedule: self.receipts.values(),
            CustomModelCatalog: [self.catalog],
            CustomModelCostSchedule: self.rules,
        }[stmt.column_descriptions[0]["entity"]]
        return SimpleNamespace(all=lambda: list(rows))

    async def merge(self, receipt):
        self.receipts[receipt.deployment_id] = receipt

    async def delete(self, receipt):
        del self.receipts[receipt.deployment_id]

    async def execute(self, statement, params):
        assert "AND NOT default_cost_managed" in str(statement)
        self.catalog.default_input_cost_per_token = params["i"]
        self.catalog.default_output_cost_per_token = params["o"]
        return SimpleNamespace(rowcount=1)

    async def commit(self):
        pass


@pytest.fixture
def store(monkeypatch):
    store = PricingStore()
    monkeypatch.setattr(job, "async_session_factory", lambda: store)
    monkeypatch.setattr(job, "_current_revisions", store.revisions)
    monkeypatch.setattr(job, "LiteLLMClient", lambda: store.proxy)
    return store


def _local_rule(days: list[int], start: int, end: int) -> CustomModelCostSchedule:
    return CustomModelCostSchedule(
        model_name="m",
        days_of_week=days,
        hour_start_local=start,
        hour_end_local=end,
        input_cost_per_token=1e-06,
        output_cost_per_token=1e-06,
        priority=0,
        enabled=True,
    )


def test_rule_matches_uses_local_weekday_no_utc_shift():
    """A KST Sat+Sun rule matches Sat/Sun and not Fri — no off-by-one day shift.

    Regression for the bug where picking 토일 stored/showed 금토: rules are now
    matched against `now` already converted to the schedule timezone, so the
    weekday is taken verbatim (no UTC round-trip that rolled the day back).
    """
    kst = ZoneInfo("Asia/Seoul")
    rule = _local_rule([6, 7], 0, 24)  # Sat+Sun, all day, in KST
    sat = datetime(2026, 6, 20, 3, 0, tzinfo=kst)  # Saturday (isoweekday 6)
    sun = datetime(2026, 6, 21, 23, 0, tzinfo=kst)  # Sunday (7)
    fri = datetime(2026, 6, 19, 3, 0, tzinfo=kst)  # Friday (5)
    assert sat.isoweekday() == 6 and sun.isoweekday() == 7 and fri.isoweekday() == 5
    assert job._rule_matches(rule, sat) is True
    assert job._rule_matches(rule, sun) is True
    assert job._rule_matches(rule, fri) is False


@pytest.mark.asyncio
async def test_keep_direct_price_equal_to_inactive_rule(store):
    store.deploy("a", 8, 9)
    result = await job.apply_cost_schedule()
    assert result["refreshed_defaults"] == 1
    assert store.catalog.default_input_cost_per_token == 8
    assert store.catalog.default_output_cost_per_token == 9
    assert not store.receipts
    store.proxy.update_model.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_only_deployments_and_fields_actually_written(store):
    store.deploy("a", 8, 9)
    store.deploy("b", 8, 0)
    store.rule.enabled = True
    assert (await job.apply_cost_schedule())["deployments_updated"] == 1
    assert set(store.receipts) == {"b"}
    assert store.receipts["b"].previous_rates == {OUTPUT: 0}
    assert store.receipts["b"].applied_rates == {OUTPUT: 9}
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    store.rules.clear()
    assert (await job.apply_cost_schedule())["deployments_updated"] == 1
    store.proxy.update_model.assert_awaited_with("b", litellm_params={OUTPUT: 0})
    assert not store.receipts
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("edits", [{INPUT: 8, OUTPUT: 9}, {INPUT: 5, OUTPUT: 7}])
async def test_external_write_after_schedule_beats_old_receipt(store, edits):
    store.deploy("a", 3, 4)
    store.rule.enabled = True
    await job.apply_cost_schedule()
    store.direct("a", edits)
    store.rule.enabled = False
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    assert store.models["a"]["litellm_params"] == edits
    assert not store.receipts


@pytest.mark.asyncio
async def test_keep_original_price_through_overlapping_schedules(store):
    store.deploy("a", 0, 4)
    store.models["a"]["model_info"][CACHE] = 1
    store.rule.enabled = True
    store.rule.cache_read_cost_per_token = 0
    await job.apply_cost_schedule()
    assert store.models["a"]["litellm_params"][CACHE] == 0
    store.rule.input_cost_per_token = 6
    store.rule.cache_read_cost_per_token = None
    await job.apply_cost_schedule()
    assert store.models["a"]["litellm_params"][CACHE] == 1
    assert store.receipts["a"].previous_rates == {INPUT: 0, OUTPUT: 4}
    store.rule.enabled = False
    await job.apply_cost_schedule()
    store.proxy.update_model.assert_awaited_with("a", litellm_params={INPUT: 0, OUTPUT: 4})


@pytest.mark.asyncio
async def test_failed_apply_or_restore_retries_without_claiming_other_deployments(store):
    store.deploy("a", 3, 4)
    store.deploy("b", 0, 1)
    store.rule.enabled = True
    store.fail_writes.add("b")
    result = await job.apply_cost_schedule()
    assert result["errors"] == 1
    assert set(store.receipts) == {"a"}
    store.fail_writes = {"a"}
    store.rule.enabled = False
    store.direct("b", {INPUT: 8, OUTPUT: 9})
    assert (await job.apply_cost_schedule())["errors"] == 1
    assert set(store.receipts) == {"a"}
    store.fail_writes.clear()
    assert (await job.apply_cost_schedule())["deployments_updated"] == 1
    assert not store.receipts
    assert store.models["b"]["litellm_params"] == {INPUT: 8, OUTPUT: 9}


@pytest.mark.asyncio
async def test_managed_default_restored_after_rule_deletion_and_external_edit(store):
    store.deploy("a", 12, 15)
    store.catalog.default_cost_managed = True
    store.catalog.default_input_cost_per_token = 0
    store.rule.enabled = True
    await job.apply_cost_schedule()
    store.rules.clear()
    store.direct("a", {INPUT: 20, OUTPUT: 30})
    await job.apply_cost_schedule()
    store.proxy.update_model.assert_awaited_with("a", litellm_params={INPUT: 0, OUTPUT: 4})
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0


@pytest.mark.asyncio
async def test_config_defined_models_are_skipped_and_unspecified_cache_is_untouched(store):
    store.deploy("cfg", 1, 2, editable=False)
    store.deploy("db", 1, 2)
    store.rule.enabled = True
    assert (await job.apply_cost_schedule())["skipped_config"] == 1
    store.proxy.update_model.assert_awaited_once_with("db", litellm_params={INPUT: 8, OUTPUT: 9})
    assert set(store.receipts) == {"db"}


@pytest.mark.asyncio
async def test_no_write_for_unowned_partial_price_or_concurrent_pass(store):
    store.catalog.default_input_cost_per_token = None
    store.catalog.default_output_cost_per_token = None
    store.deploy("a", 2, None)
    store.locked = True
    await job.apply_cost_schedule()
    store.proxy.get_model_info.assert_not_called()
    store.locked = False
    await job.apply_cost_schedule()
    store.proxy.update_model.assert_not_called()


@pytest.mark.asyncio
async def test_missing_baseline_keeps_only_unrestored_field_until_price_is_available(store):
    store.deploy("a", 0, 2)
    store.rule.cache_read_cost_per_token = 1
    store.rule.enabled = True
    await job.apply_cost_schedule()
    store.rule.enabled = False
    result = await job.apply_cost_schedule()
    assert result["missing_default"] == 1
    assert store.models["a"]["litellm_params"] == {INPUT: 0, OUTPUT: 2, CACHE: 1}
    assert store.receipts["a"].previous_rates == {CACHE: None}
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    store.catalog.default_cache_read_cost_per_token = 0
    await job.apply_cost_schedule()
    store.proxy.update_model.assert_awaited_with("a", litellm_params={CACHE: 0})
    assert not store.receipts


@pytest.mark.asyncio
async def test_direct_write_during_pass_is_seen_before_restore(store, monkeypatch):
    store.deploy("a", 3, 4)
    store.rule.enabled = True
    await job.apply_cost_schedule()
    store.rule.enabled = False
    snapshot = dict(store.versions)
    store.direct("a", {INPUT: 8, OUTPUT: 9})
    monkeypatch.setattr(job, "_current_revisions", AsyncMock(side_effect=[snapshot, dict(store.versions)]))
    result = await job.apply_cost_schedule()
    assert result["deployments_updated"] == 0
    assert result["errors"] == 1
    assert "a" in store.receipts
    monkeypatch.setattr(job, "_current_revisions", store.revisions)
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    assert not store.receipts
