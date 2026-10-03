"""Serving home aggregation (pure) + endpoint auth."""

import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.api.serving_overview import build_overview


def _rows(*a, **kw):
    return build_overview(*a, **kw)["models"]


def _dep(**kw):
    base = dict(
        namespace="ns",
        id=uuid.uuid4(),
        model_name="m",
        model_path="/models/m",
        status="Ready",
        ready_replicas=1,
        replicas=1,
        engine="vllm",
        litellm_model_id="lm-1",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _run(**kw):
    base = dict(
        id=uuid.uuid4(),
        model_name="m",
        kind="performance",
        tool="vllm_serving",
        status="succeeded",
        finished_at=datetime(2026, 9, 1, tzinfo=UTC),
        result={"metrics": {"output_throughput": 120.5, "mean_ttft_ms": 40.0, "extra": 1}},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _recipe(**kw):
    base = dict(id=uuid.uuid4(), name="r", model_path="/models/m", engine="sglang")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cat(**kw):
    base = dict(id=uuid.uuid4(), model_name="m", display_name="M", status="lts", visible=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="stack-m",
        target_model_name="m",
        values_snapshot={"router": {"modelServers": {"matchLabels": {"llm-d.ai/model": "m"}}}},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_row_joins_every_stage():
    rows = _rows(
        [_dep()], [_stack()], [_run(), _run(kind="accuracy", result={"acc": 0.81, "note": "x"})], [_recipe()], [_cat()]
    )
    assert [r["model_name"] for r in rows] == ["m"]
    r = rows[0]
    assert r["recipes"][0]["engine"] == "sglang"
    assert r["deployments"][0]["status"] == "Ready" and r["deployments"][0]["engine"] == "vllm"
    assert r["llmd_stacks"][0]["name"] == "stack-m"
    assert r["performance"]["output_throughput"] == 120.5 and r["performance"]["mean_ttft_ms"] == 40.0
    assert "extra" not in r["performance"]
    assert r["accuracy"]["metric"] == {"name": "acc", "value": 0.81}
    assert r["catalog"]["status"] == "lts"
    assert r["litellm_registered"] is True


def test_models_without_deployment_still_listed_and_sorted():
    zeta = _stack(
        name="zeta-stack",
        target_model_name="zeta",
        values_snapshot={"router": {"modelServers": {"matchLabels": {"llm-d.ai/model": "zeta"}}}},
    )
    out = build_overview([], [zeta], [_run(model_name="alpha", kind="accuracy", result={"accuracy": 0.5})], [], [])
    rows = out["models"]
    assert [r["model_name"] for r in rows] == ["alpha"]
    assert rows[0]["deployments"] == [] and rows[0]["catalog"] is None and rows[0]["litellm_registered"] is False
    assert rows[0]["accuracy"]["metric"] == {"name": "accuracy", "value": 0.5}
    # A stack whose selector picks no known server is reported, not silently attached by name.
    assert [u["name"] for u in out["unlinked_stacks"]] == ["zeta-stack"]
    assert out["unlinked_stacks"][0]["target_model_name"] == "zeta"


def test_stack_links_by_selector_not_by_target_name():
    dep = _dep(model_name="cpu-demo")
    legacy = _stack(
        name="legacy",
        target_model_name="cpu-demo",
        values_snapshot={
            "inferenceExtension": {"endpointsServer": {"endpointSelector": "llm-ops/model-name=cpu-demo"}}
        },
    )
    wrong = _stack(
        name="wrong",
        target_model_name="cpu-demo",
        values_snapshot={"router": {"modelServers": {"matchLabels": {"app": "x"}}}},
    )
    out = build_overview(
        [dep], [legacy, wrong], [], [], [], stack_status={str(legacy.id): {"health_status": "Healthy"}}
    )
    row = out["models"][0]
    assert [s["name"] for s in row["llmd_stacks"]] == ["legacy"]
    assert row["llmd_stacks"][0]["health_status"] == "Healthy"
    assert row["llmd_stacks"][0]["servers"] == [{"kind": "portal", "name": "cpu-demo", "namespace": "ns"}]
    assert [u["name"] for u in out["unlinked_stacks"]] == ["wrong"]


def test_external_serving_links_through_registration():
    ext = {
        "deployment_name": "vllm-qwen",
        "namespace": "ml",
        "status": "Ready",
        "labels": {"llm-d.ai/model": "Qwen3-32B"},
    }
    reg = types.SimpleNamespace(namespace="ml", deployment_name="vllm-qwen", model_name="qwen3-32b")
    st = _stack(
        name="ext",
        target_model_name="qwen3-32b",
        values_snapshot={"router": {"modelServers": {"matchLabels": {"llm-d.ai/model": "Qwen3-32B"}}}},
    )
    out = build_overview([], [st], [], [], [], external_servings=[ext], external_registrations=[reg])
    assert [r["model_name"] for r in out["models"]] == ["qwen3-32b"]
    assert out["models"][0]["llmd_stacks"][0]["servers"][0]["kind"] == "external"


def test_latest_succeeded_run_wins_and_flat_result_supported():
    newer = _run(finished_at=datetime(2026, 9, 5, tzinfo=UTC), result={"output_throughput": 200.0})
    older = _run(finished_at=datetime(2026, 9, 1, tzinfo=UTC), result={"output_throughput": 100.0})
    rows = _rows([_dep()], [], [newer, older], [], [])
    assert rows[0]["performance"]["output_throughput"] == 200.0


def test_recipes_match_by_model_path_only():
    rows = _rows(
        [_dep(model_path="/models/a")], [], [], [_recipe(model_path="/models/a"), _recipe(model_path="/models/b")], []
    )
    assert len(rows[0]["recipes"]) == 1


async def test_overview_requires_super_user(client_for_user, regular_user, mock_db):
    async with client_for_user(regular_user) as client:
        resp = await client.get("/api/admin/serving/overview")
    assert resp.status_code == 403


async def test_overview_endpoint_shape(client_for_user, super_user, mock_db):
    def _res(rows):
        r = MagicMock()
        r.scalars.return_value.all.return_value = rows
        return r

    mock_db.execute = AsyncMock(
        side_effect=[_res([_dep()]), _res([]), _res([]), _res([]), _res([_cat()]), _res([]), _res([])]
    )
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/serving/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["models"][0]["model_name"] == "m" and body["models"][0]["catalog"]["status"] == "lts"
    assert body["unlinked_stacks"] == []
