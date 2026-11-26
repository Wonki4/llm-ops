import os
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api import model_demand as api
from app.db.models.custom_user import GlobalRole
from app.services.model_demand import (
    Consumer,
    DemandScope,
    Forecast,
    counted_tpm,
    forecasts,
    observed,
    scope_key,
    split_capacity,
)

ADMIN = SimpleNamespace(user_id="ops", litellm_user_id=None, global_role=GlobalRole.SUPER_USER)
USER = SimpleNamespace(user_id="u1", litellm_user_id=None, global_role=GlobalRole.USER)
TEAM = {"scope": "team", "subject": "A", "model": "shared/model"}


def forecast(**changes):
    fields = dict(
        **TEAM,
        period_start="2026-01-01",
        period_end="2026-12-31",
        mode="per_request",
        rpm=6,
        input_tokens=100_000,
        output_tokens=1_000,
    )
    return Forecast(**(fields | changes))


def test_forecast_period_and_units():
    per_request = forecast()
    assert per_request.rates() == {"rpm": 6, "input_tpm": 600_000, "output_tpm": 6_000}
    assert forecast(mode="per_minute").rates() == {"rpm": 6, "input_tpm": 100_000, "output_tpm": 1000}
    assert scope_key(per_request) != scope_key(forecast(model="other"))
    with pytest.raises(ValidationError):
        forecast(period_end="2024-01-02")
    with pytest.raises(ValidationError):
        forecast(rpm=0)


def test_shared_capacity_reserve_and_accounting():
    rates = dict(rpm=10, input_tpm=500, output_tpm=200)
    demand = dict(rpm=8, input_tpm=100, output_tpm=200)
    result = split_capacity(rates, [demand, demand], 20)
    assert result["limiting_dimension"] == "output_tpm"
    assert result["recommendations"] == [dict(rpm=3, input_tpm=40, output_tpm=80)] * 2
    assert [counted_tpm(demand, k) for k in ("input", "output", "total", None)] == [100, 200, 300, None]


async def test_permissions_and_overlapping_consumers():
    with pytest.raises(HTTPException, match="private"):
        await api.authorize(DemandScope(scope="user", subject="other", model="a"), USER, None)
    with pytest.raises(HTTPException, match="Overlapping"):
        await api.disjoint_consumers(
            [Consumer(**TEAM), Consumer(scope="user", subject="u1", model=TEAM["model"])], None
        )
    await api.disjoint_consumers(
        [Consumer(**TEAM), Consumer(scope="personal", subject="u1", model=TEAM["model"])], None
    )
    with pytest.raises(ValidationError):
        api.Simulation(run_id=uuid4(), consumers=[Consumer(**TEAM), Consumer(**TEAM)])


def test_baseline_requires_isolation_and_exclusive_live_routes(monkeypatch):
    monkeypatch.setattr(api, "measurement_matches", lambda run, dep: True)
    from app.api import benchmarks

    monkeypatch.setattr(benchmarks, "_serving_snapshot", lambda _: {"image": "A", "engine_args": {"dtype": "auto"}})
    monkeypatch.setattr(
        api, "registration_preview", lambda _: {"api_base": "https://server/v1", "litellm_model": "hosted_vllm/a"}
    )
    dep = SimpleNamespace(
        id=uuid4(),
        cluster_id=uuid4(),
        namespace="ns",
        ready_replicas=1,
        status="Ready",
        serving_mode="aggregated",
        replicas=1,
        model_name="a",
    )
    result = {
        "scope": "deployment",
        "confirmed": True,
        "best": {"slo_rates": {"rpm": 6, "input_tpm": 600000, "output_tpm": 6000}},
    }
    run = SimpleNamespace(
        id=uuid4(),
        cluster_id=dep.cluster_id,
        k8s_namespace="ns",
        serving_snapshot={"image": "A", "engine_args": {"dtype": "auto"}},
        ephemeral=True,
        validation_attempt_id=None,
        result={"capacity_search": result},
        params={},
        created_at=None,
    )

    def row(name, base):
        return {"model_name": name, "litellm_params": {"api_base": base, "model": "hosted_vllm/a"}}

    rows = [
        row("a", "https://server/v1"),
        row("b", "https://server/v1/"),
        row("mixed", "https://server/v1"),
        row("mixed", "https://other/v1"),
    ]
    assert api.eligible_capacity(run, dep, rows)["aliases"] == ["a", "b"]
    run.serving_snapshot["image"] = "old"
    assert api.eligible_capacity(run, dep, rows) is None
    run.serving_snapshot["image"] = "A"
    run.ephemeral = False
    assert api.eligible_capacity(run, dep, rows) is None
    run.ephemeral = True
    result["scope"] = "endpoint"
    assert api.eligible_capacity(run, dep, rows) is None


async def test_simulation_never_writes_and_unknown_tpm_stays_unknown(monkeypatch):
    rates = forecast().rates()
    monkeypatch.setattr(
        api, "capacities", AsyncMock(return_value=([{"aliases": [TEAM["model"], "b"], "rates": rates}], None))
    )
    monkeypatch.setattr(api, "forecasts", AsyncMock(return_value=[{"rates": rates}]))
    monkeypatch.setattr(api, "token_accounting", AsyncMock(return_value=None))
    monkeypatch.setattr(api, "live_limits", AsyncMock(return_value={"rpm": 1, "tpm": 2}))
    proxy = AsyncMock()
    result = await api.simulate(
        api.Simulation(run_id=uuid4(), consumers=[Consumer(**TEAM), Consumer(**(TEAM | {"model": "b"}))]),
        ADMIN,
        AsyncMock(),
        AsyncMock(),
        proxy,
    )
    assert result["available"]["rpm"] == pytest.approx(4.8)
    assert [c["recommended"]["rpm"] for c in result["consumers"]] == [2, 2]
    assert result["consumers"][0]["counted_tpm"] is None and result["basis"] == "scenario"
    proxy.update_team.assert_not_called()
    proxy.update_key.assert_not_called()


async def test_scoped_save_merges_effective_maps_and_rechecks_live_value(monkeypatch):
    row = {
        "team_id": "A",
        "metadata": {},
        "inherited": {"model_rpm_limit": {"shared/model": 10, "other": 12}, "model_tpm_limit": {"other": 100}},
    }
    monkeypatch.setattr(api, "key_record", AsyncMock(return_value=row))
    target = api.ModelLimitPatch(
        scope="key", subject="hash", model="shared/model", rpm=4, tpm=120, expected_rpm=10, expected_tpm=None
    )
    proxy = AsyncMock()
    with pytest.raises(HTTPException, match="inherits"):
        await api.save_model_limits(target, ADMIN, AsyncMock(), None, proxy)
    proxy.update_key.assert_not_called()
    row["metadata"] = row["inherited"]
    row["inherited"] = {}
    await api.save_model_limits(target, ADMIN, AsyncMock(), None, proxy)
    proxy.update_key.assert_awaited_once_with(
        "hash", model_rpm_limit={"shared/model": 4, "other": 12}, model_tpm_limit={"shared/model": 120, "other": 100}
    )
    target.expected_rpm = 9
    with pytest.raises(HTTPException, match="changed"):
        await api.save_model_limits(target, ADMIN, AsyncMock(), None, proxy)
    for scope in ("user", "personal"):
        target.scope = scope
        with pytest.raises(HTTPException, match="per-model"):
            await api.save_model_limits(target, ADMIN, AsyncMock(), None, proxy)


@pytest.fixture
async def postgres():
    url = os.environ.get("MODEL_DEMAND_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set MODEL_DEMAND_TEST_DATABASE_URL to exercise PostgreSQL aggregation and storage")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        async with AsyncSession(bind=conn) as db:
            await db.execute(
                text("""CREATE TEMP TABLE "LiteLLM_SpendLogs"(
                "startTime" timestamp, "user" text, team_id text, api_key text,
                model_group text, prompt_tokens integer, completion_tokens integer,status text)""")
            )
            await db.execute(
                text("""CREATE TEMP TABLE custom_portal_settings(
                key varchar(128) PRIMARY KEY,value text,updated_at timestamptz DEFAULT now(),updated_by text)""")
            )
            try:
                yield db
            finally:
                await db.rollback()
    await engine.dispose()


async def test_observed_request_minutes_and_forecasts_in_postgres(postgres):
    db = postgres
    await db.execute(
        text("""INSERT INTO "LiteLLM_SpendLogs" VALUES
      ('2026-06-01 10:10:01','u1','A','k','shared/model',100,1,'success'),
      ('2026-06-01 10:10:59','u2','A','k2','shared/model',200,2,'failure'),
      ('2026-06-01 10:16:00','u1','A','k','shared/model',0,0,'success'),
      ('2026-06-01 10:15:00','u1','A','k','',8,4,'success'),
      ('2026-06-01 10:10:05','u1','','p','shared/model',999,99,'success'),
      ('2026-06-02 00:00:01','u1','A','k','shared/model',999,99,'success')""")
    )
    target = DemandScope(**TEAM)
    now = datetime(2026, 6, 2, tzinfo=UTC)
    actual = await observed(db, target, 7, now)
    assert actual["peak"] == {"rpm": 2, "input_tpm": 300, "output_tpm": 3}
    assert actual["p95"] == actual["peak"]
    assert actual["active_minutes"] == 2 and actual["errors"] == 1 and actual["unassigned"] == 1
    assert actual["first_seen"] == datetime(2026, 6, 1, 10, 10, 1)
    absent = await observed(db, DemandScope(**(TEAM | {"model": "no calls"})), 7, now)
    assert absent["peak"] is None
    personal = await observed(db, DemandScope(scope="personal", subject="u1", model=target.model), 7, now)
    assert personal["peak"] == {"rpm": 1, "input_tpm": 999, "output_tpm": 99}
    await db.execute(text("INSERT INTO custom_portal_settings(key,value) VALUES ('default_team_id','not JSON')"))
    body = forecast()
    await api.submit(body, ADMIN, db, None)
    await api.submit(body, ADMIN, db, None)
    await api.submit(forecast(subject="B"), ADMIN, db, None)
    assert (await forecasts(db, target, date(2026, 6, 1)))[0]["rates"] == body.rates()
    assert (await forecasts(db, target, date(2027, 6, 1))) == []
    assert len(await forecasts(db, target)) == 1


async def test_published_validation_producer_to_allocation():
    """Exercise the actual producer when the independent capacity-search PR is installed."""
    pytest.importorskip("app.services.capacity_identity")
    import time
    from unittest.mock import Mock

    from app.services import portal_bench_runner as runner
    from app.services.validation_runtime import prepare_benchmark

    attempt = SimpleNamespace(id=uuid4())
    dep = api.CustomModelDeployment(
        id=uuid4(),
        cluster_id=uuid4(),
        namespace="tenant-ns",
        model_name="candidate-x",
        model_path="/models/a",
        engine="vllm",
        image="engine:1",
        engine_args={},
        env={"MODE": "a"},
        replicas=2,
        ready_replicas=2,
        status="Ready",
        serving_mode="aggregated",
        gpu_count=1,
        gpu_resource_key="nvidia.com/gpu",
        ingress_host="model.example",
        ingress_path="/",
        validation_state="candidate",
        validation_attempt_id=attempt.id,
    )
    job = SimpleNamespace(
        id=uuid4(), cluster_id=dep.cluster_id, namespace=dep.namespace, model_name="public", created_by="ops"
    )
    slo = dict(ttft_ms=100, tpot_ms=None, e2el_ms=None, min_slo_percent=95, max_error_percent=0)
    options = dict(max_rate=1, max_lag_ms=100, refinements=1, repeats=3)
    db = AsyncMock()
    db.add = Mock()
    db.get.return_value = None
    run = await prepare_benchmark(
        db,
        job,
        SimpleNamespace(name="fixture"),
        attempt,
        dep,
        "capacity-search",
        {"slo": slo, "capacity_search": options, "random_input_len": 100_000, "random_output_len": 1000},
    )

    async def measure(rate, index):
        sample = runner.Sample(100_000, 1000, 40, 400, 10, [])
        return {
            **runner.summarize([sample] * 60, 600, None, slo),
            "requested": 60,
            "target_arrivals_per_sec": rate,
            "actual_arrivals_per_sec": rate,
            "p99_runner_delay_ms": 0,
        }

    result = {}
    await runner.search_rates(run.params, options, result, measure, time.monotonic() + 30, "deployment")
    run.result = result
    upstream = api.registration_preview(dep)
    conns = [
        {"model_name": name, "litellm_params": {"api_base": upstream["api_base"], "model": upstream["litellm_model"]}}
        for name in ("A", "B")
    ]
    assert api.eligible_capacity(run, dep, conns) is None
    dep.validation_state = "published"
    dep.litellm_model_id = str(uuid4())
    entry = api.eligible_capacity(run, dep, conns)
    assert entry["rates"] == {"rpm": 6, "input_tpm": 600000, "output_tpm": 6000}
    assert entry["aliases"] == ["A", "B"] and entry["repetitions"] == 3
    dep.ready_replicas = 1
    assert api.eligible_capacity(run, dep, conns) is None
    dep.ready_replicas = 2
    dep.env = {"MODE": "b"}
    assert api.eligible_capacity(run, dep, conns) is None


def test_workload_mixes_require_measured_proportions():
    from app.services.model_demand import workload_basis

    consumer = Consumer(**TEAM, workload_match=True)
    demands = [dict(rpm=4, input_tpm=400, output_tpm=80), dict(rpm=4, input_tpm=4000, output_tpm=8)]
    base = {"rates": dict(rpm=80, input_tpm=44000, output_tpm=880)}
    assert workload_basis(base, [consumer] * 2, demands) == "scenario"
    base["mixed_profiles"] = [
        dict(input_per_request=100, output_per_request=20, request_fraction=0.5),
        dict(input_per_request=1000, output_per_request=2, request_fraction=0.5),
    ]
    assert workload_basis(base, [consumer] * 2, demands) == "measured_mix"
    assert workload_basis(base, [consumer], [demands[0]]) == "scenario"
