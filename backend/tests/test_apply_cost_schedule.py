"""Time windows are applied to each deployment's native LiteLLM billing rules."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.clients.litellm import LiteLLMClient
from app.db.models.custom_model_cost_schedule import CustomModelCostSchedule
from app.jobs import apply_cost_schedule as job

INPUT = "input_cost_per_token"
OUTPUT = "output_cost_per_token"
CACHE = "cache_read_input_token_cost"
MONDAY = datetime(2026, 11, 16, 15, tzinfo=UTC)  # midnight Tuesday in Seoul


def rule(days, start, end, price=8, cache=None):
    return CustomModelCostSchedule(
        model_name="m",
        days_of_week=days,
        hour_start_local=start,
        hour_end_local=end,
        input_cost_per_token=price,
        output_cost_per_token=9,
        cache_read_cost_per_token=cache,
        enabled=True,
        priority=0,
    )


def compile_rules(*rules, now=MONDAY, zone="Asia/Seoul"):
    return job._native_schedule(rules, list(job._week_slots(now, ZoneInfo(zone))), now)


def test_overnight_and_overlap_end_at_correct_utc_boundary():
    high = rule([1], 23, 2, price=0, cache=0)
    low = rule([1], 22, 6)
    block, active = compile_rules(high, low)
    assert active and block[INPUT] == block[CACHE] == 0
    assert block["windows"] == [{"weekdays": [1], "hours_utc": "14:00-17:00"}]
    block, active = compile_rules(high, low, now=MONDAY + timedelta(hours=2))
    assert active and block[CACHE] is None
    assert block["windows"] == [
        {"weekdays": [1], "hours_utc": "13:00-14:00"},
        {"weekdays": [1], "hours_utc": "17:00-21:00"},
    ]
    _, active = compile_rules(high, low, now=MONDAY + timedelta(hours=6))
    assert not active


def test_midnight_and_fractional_timezone():
    block, _ = compile_rules(rule([2], 0, 12), zone="Asia/Kathmandu")
    assert block["windows"] == [
        {"weekdays": [1], "hours_utc": "18:15-00:00"},
        {"weekdays": [2], "hours_utc": "00:00-06:15"},
    ]


def test_dst_fall_back_includes_both_occurrences_of_local_hour():
    now = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    block, active = compile_rules(rule([7], 1, 2), zone="America/New_York", now=now)
    assert active and block["windows"] == [{"weekdays": [7], "hours_utc": "05:00-07:00"}]


def test_external_schedule_is_preserved_and_restored():
    external = {"hours_utc": "03:00-05:00", INPUT: 3}
    block, _ = compile_rules(rule([2], 0, 3))
    assert job._desired_schedule(external, block, False) == external
    managed = job._desired_schedule(external, block, True)
    assert managed[job.PREVIOUS_SCHEDULE] == external
    assert managed["hours_utc"] == []
    assert job._desired_schedule(managed, block, True) == managed
    restored = job._desired_schedule(managed, None, False)
    assert restored["hours_utc"] == external["hours_utc"] and restored[INPUT] == 3
    assert restored["windows"] == [] and restored[OUTPUT] is None


def deployment(mid, cost):
    return {"model_name": "m", "model_info": {"id": mid, "db_model": True}, "litellm_params": {INPUT: cost, OUTPUT: 4}}


@pytest.fixture
def worker(monkeypatch):
    proxy = MagicMock(spec=LiteLLMClient)
    db = AsyncMock()
    rules, receipts = [], []
    db.scalars.side_effect = lambda query: (
        rules if query.column_descriptions[0]["entity"] is CustomModelCostSchedule else receipts
    )
    factory = MagicMock()
    factory.begin.return_value.__aenter__.return_value = db
    monkeypatch.setattr(job, "async_session_factory", factory)
    monkeypatch.setattr(job, "LiteLLMClient", lambda: proxy)
    return SimpleNamespace(proxy=proxy, db=db, rules=rules, receipts=receipts)


async def test_only_patch_windows_per_deployment_even_after_direct_base_edits(monkeypatch, worker):
    a, b, config = (deployment(mid, cost) for mid, cost in [("a", 3), ("b", 0), ("config", 4)])
    config["model_info"]["db_model"] = False
    proxy, db = worker.proxy, worker.db
    proxy.get_model_info.return_value = [a, b, config]

    async def patch(mid, block, *, legacy_restore):
        assert legacy_restore is None
        {"a": a, "b": b}[mid]["model_info"]["off_peak_pricing"] = block

    proxy.set_cost_schedule.side_effect = patch
    worker.rules.append(rule(range(1, 8), 0, 24))
    monkeypatch.setattr(job, "_current_revisions", AsyncMock(side_effect=AssertionError("unexpected base-price read")))
    db.scalar.return_value = False
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    proxy.get_model_info.assert_not_called()
    db.scalar.return_value = True
    result = await job.apply_cost_schedule()
    assert result["deployments_updated"] == 2 and result["skipped_config"] == 1
    a["litellm_params"][INPUT] = 12
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0
    worker.rules.clear()
    assert (await job.apply_cost_schedule())["deployments_updated"] == 2
    assert a["model_info"]["off_peak_pricing"] == job.EMPTY_SCHEDULE
    assert [x["litellm_params"][INPUT] for x in (a, b, config)] == [12, 0, 4]
    assert (await job.apply_cost_schedule())["deployments_updated"] == 0


@pytest.mark.parametrize(
    ("revision", "live", "expected"),
    [
        (MONDAY, 8, {INPUT: None, OUTPUT: 0}),
        (MONDAY + timedelta(seconds=1), 8, None),
        (MONDAY, 13, None),
    ],
)
async def test_legacy_handoff_only_reverts_a_confirmed_write(monkeypatch, worker, revision, live, expected):
    worker.proxy.get_model_info.return_value = [deployment("a", live)]
    receipt = SimpleNamespace(
        deployment_id="a",
        model_name="m",
        litellm_updated_at=MONDAY,
        applied_rates={INPUT: 8, OUTPUT: 4},
        previous_rates={INPUT: None, OUTPUT: 0},
    )
    worker.receipts.append(receipt)
    worker.rules.append(rule(range(1, 8), 0, 24))
    monkeypatch.setattr(job, "_current_revisions", AsyncMock(return_value={"a": revision}))
    result = await job.apply_cost_schedule()
    assert result["errors"] == 0
    assert worker.proxy.set_cost_schedule.await_args.kwargs["legacy_restore"] == expected
    worker.db.delete.assert_awaited_once_with(receipt)


async def test_failed_handoff_keeps_receipt_for_retry(monkeypatch, worker):
    worker.proxy.get_model_info.return_value = [deployment("a", 3)]
    worker.proxy.set_cost_schedule.side_effect = RuntimeError("offline")
    worker.receipts.append(
        SimpleNamespace(
            deployment_id="a",
            model_name="m",
            litellm_updated_at=MONDAY,
            applied_rates={INPUT: 3},
            previous_rates={INPUT: 0},
        )
    )
    monkeypatch.setattr(job, "_current_revisions", AsyncMock(return_value={"a": MONDAY}))
    assert (await job.apply_cost_schedule())["errors"] == 1
    worker.db.delete.assert_not_awaited()


async def test_only_retire_unlisted_receipts_for_confirmed_deletions(worker, monkeypatch):
    deleted, hidden = (SimpleNamespace(deployment_id=mid) for mid in ("deleted", "hidden"))
    worker.proxy.get_model_info.return_value = []
    worker.receipts.extend([deleted, hidden])
    monkeypatch.setattr(job, "_current_revisions", AsyncMock(return_value={"hidden": MONDAY}))
    assert (await job.apply_cost_schedule())["errors"] == 0
    worker.db.delete.assert_awaited_once_with(deleted)
    worker.proxy.set_cost_schedule.assert_not_called()


async def test_native_update_payload_never_echoes_litellm_params():
    proxy = LiteLLMClient()
    proxy._request = AsyncMock()
    await proxy.set_cost_schedule("m", job.EMPTY_SCHEDULE)
    proxy._request.assert_awaited_once_with(
        "PATCH", "/model/m/update", json={"model_info": {"id": "m", "off_peak_pricing": job.EMPTY_SCHEDULE}}
    )
