"""Reconciler: prefill/decode servings are observed per role and registered via the router."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.jobs import reconcile_deployments as rd


def _dep(**kw):
    base = dict(
        id=uuid.uuid4(),
        model_name="glm",
        model_path="/models/glm",
        namespace="default",
        cluster_id=None,
        image="vllm/vllm-openai:latest",
        replicas=2,
        gpu_count=8,
        gpu_resource_key="nvidia.com/gpu",
        engine="vllm",
        engine_args={},
        vllm_extra_args=None,
        env=None,
        probes=None,
        runtime=None,
        serving_mode="pd",
        pd_config={"prefill": {"replicas": 1}, "decode": {"replicas": 2}},
        pd_status=None,
        router_stack_id=uuid.uuid4(),
        router_stack_created=True,
        ingress_host="",
        ingress_path="/",
        status="Pending",
        status_message=None,
        ready_replicas=0,
        service_cluster_ip=None,
        last_synced_at=None,
        litellm_model_id=None,
        updated_by="admin",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _k8s(prefill=None, decode=None):
    """K8s mock whose read_deployment_status answers per role (None → 404)."""
    ready = {"glm-prefill-deployment": prefill, "glm-decode-deployment": decode}

    async def read(ns, name):
        obs = ready[name]
        if obs is None:
            raise ApiException(status=404)
        return obs

    k8s = MagicMock()
    k8s.read_deployment_status = AsyncMock(side_effect=read)
    k8s.read_service_cluster_ip = AsyncMock(return_value="10.0.0.9")
    return k8s


def _obs(ready, desired, available=None):
    return {
        "ready": ready,
        "desired": desired,
        "available": ready if available is None else available,
        "conditions": [],
    }


def _db(stack=None):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.get = AsyncMock(return_value=stack)
    res = MagicMock()
    res.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=res)
    return db


def _stack(health="Healthy", host=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name="glm-router",
        argo_app_name="llmd-glm-router",
        ingress_host=host,
        cluster_id=None,
        namespace="default",
        _health=health,
    )


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch):
    monkeypatch.setattr(rd, "send_deployment_event_notification", AsyncMock(return_value=False))

    async def live_status(db, stack):
        return {"sync_status": "Synced", "health_status": stack._health, "status_message": None}

    monkeypatch.setattr(rd.llmd_stacks, "live_status", live_status)


# ─── observation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "prefill, decode, expected_status, expected_msg",
    [
        (_obs(1, 1), _obs(2, 2), "Ready", None),
        (_obs(1, 1), _obs(0, 2), "Pending", "decode: No ready pods yet"),
        (_obs(1, 1), _obs(1, 2), "Updating", "decode: 1/2 pods ready"),
        (
            {
                "ready": 0,
                "desired": 1,
                "available": 0,
                "conditions": [{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded"}],
            },
            _obs(2, 2),
            "Failed",
            "prefill: Deployment progress deadline exceeded",
        ),
        (_obs(1, 1), None, "Unhealthy", "decode: K8s Deployment not found"),
        (None, None, "Missing", "K8s Deployments not found"),
    ],
)
async def test_observe_pd_status_table(prefill, decode, expected_status, expected_msg):
    status, msg, per_role = await rd._observe_pd(_k8s(prefill, decode), _dep())
    assert (status, msg) == (expected_status, expected_msg)
    assert set(per_role) == {"prefill", "decode"}
    assert per_role["prefill"]["desired"] == 1 and per_role["decode"]["desired"] == 2


async def test_observe_pd_reraises_non_404():
    k8s = MagicMock()
    k8s.read_deployment_status = AsyncMock(side_effect=ApiException(status=500))
    with pytest.raises(ApiException):
        await rd._observe_pd(k8s, _dep())


# ─── reconcile pass ───────────────────────────────────────────────────────────


async def test_pd_pass_stores_per_role_status_and_mirrors_decode():
    dep = _dep()
    db = _db(stack=_stack())
    litellm = MagicMock()
    litellm.create_model = AsyncMock(return_value={"model_info": {"id": "m-1"}})
    transitions, registered = await rd._reconcile_pd(db, litellm, _k8s(_obs(1, 1), _obs(1, 2)), dep)
    assert (transitions, registered) == (1, 0)
    assert dep.status == "Updating" and dep.ready_replicas == 1 and dep.service_cluster_ip == "10.0.0.9"
    assert dep.pd_status["decode"] == {"ready": 1, "desired": 2, "status": "Updating", "message": "1/2 pods ready"}
    assert dep.pd_status["prefill"]["status"] == "Ready"
    litellm.create_model.assert_not_awaited()


async def test_pd_ready_registers_via_router_host_when_healthy():
    dep = _dep()
    db = _db(stack=_stack(host="glm.example.com"))
    litellm = MagicMock()
    litellm.create_model = AsyncMock(return_value={"model_info": {"id": "m-1"}})
    transitions, registered = await rd._reconcile_pd(db, litellm, _k8s(_obs(1, 1), _obs(2, 2)), dep)
    assert (transitions, registered) == (1, 1)
    assert dep.status == "Ready" and dep.litellm_model_id == "m-1"
    kwargs = litellm.create_model.await_args.kwargs
    assert kwargs["api_base"] == "https://glm.example.com"
    assert kwargs["model_name"] == "glm" and kwargs["litellm_model"] == "openai/glm"
    assert dep.pd_status["router"] == {"ready": True, "reason": None, "api_base": "https://glm.example.com"}


async def test_pd_ready_waits_for_router_then_registers_once(monkeypatch):
    dep = _dep()
    stack = _stack(health="Progressing")
    db = _db(stack=stack)
    litellm = MagicMock()
    litellm.create_model = AsyncMock(return_value={"model_info": {"id": "m-2"}})
    k8s = _k8s(_obs(1, 1), _obs(2, 2))

    # pass 1: serving Ready, router Progressing → Ready but not registered, waiting message
    transitions, registered = await rd._reconcile_pd(db, litellm, k8s, dep)
    assert (transitions, registered) == (1, 0)
    assert dep.status == "Ready" and dep.litellm_model_id is None
    assert dep.status_message.startswith("Serving ready; waiting for the router: router 'glm-router' is Progressing")
    assert dep.pd_status["router"]["ready"] is False

    # pass 2: still Progressing → no registration attempt, no new transition
    transitions, registered = await rd._reconcile_pd(db, litellm, k8s, dep)
    assert (transitions, registered) == (0, 0)
    litellm.create_model.assert_not_awaited()

    # pass 3: router Healthy → registered once; default host = <argo app>.<domain>
    stack._health = "Healthy"
    monkeypatch.setattr(rd.llmd_stacks, "ingress_host", lambda s: f"{s.argo_app_name}.llm.example.com")
    transitions, registered = await rd._reconcile_pd(db, litellm, k8s, dep)
    assert (transitions, registered) == (0, 1)
    assert dep.litellm_model_id == "m-2" and dep.status_message is None
    assert litellm.create_model.await_args.kwargs["api_base"] == "https://llmd-glm-router.llm.example.com"

    # pass 4: nothing more to do
    transitions, registered = await rd._reconcile_pd(db, litellm, k8s, dep)
    assert (transitions, registered) == (0, 0)
    assert litellm.create_model.await_count == 1


async def test_pd_without_router_stack_never_registers():
    dep = _dep(router_stack_id=None)
    db = _db()
    litellm = MagicMock()
    litellm.create_model = AsyncMock()
    await rd._reconcile_pd(db, litellm, _k8s(_obs(1, 1), _obs(2, 2)), dep)
    assert dep.status == "Ready" and "no llm-d router stack" in dep.status_message
    litellm.create_model.assert_not_awaited()


async def test_pd_register_failure_records_error_event_once():
    dep = _dep()
    db = _db(stack=_stack(host="r.example.com"))
    litellm = MagicMock()
    litellm.create_model = AsyncMock(side_effect=RuntimeError("boom"))
    k8s = _k8s(_obs(1, 1), _obs(2, 2))
    await rd._reconcile_pd(db, litellm, k8s, dep)
    events = [a.args[0] for a in db.add.call_args_list if getattr(a.args[0], "event_type", None)]
    assert [e.event_type for e in events] == ["StatusChanged", "LitellmRegisterFailed"]
    await rd._reconcile_pd(db, litellm, k8s, dep)  # no retry until the next Ready transition
    assert litellm.create_model.await_count == 1


async def test_reconcile_once_routes_pd_rows_to_pd_pass(monkeypatch):
    dep = _dep()
    agg = _dep(
        model_name="agg", serving_mode="aggregated", router_stack_id=None, pd_config=None, ingress_host="agg.example.com"
    )
    db = _db(stack=_stack(host="r.example.com"))
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [dep, agg]
    db.execute = AsyncMock(return_value=rows)
    db.commit = AsyncMock()

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(rd, "async_session_factory", lambda: _Session())
    k8s = _k8s(_obs(1, 1), _obs(2, 2))
    k8s.read_deployment_status = AsyncMock(
        side_effect=lambda ns, name: _obs(2, 2) if "agg" in name else (_obs(1, 1) if "prefill" in name else _obs(2, 2))
    )
    monkeypatch.setattr(rd, "k8s_for_cluster", AsyncMock(return_value=k8s))
    monkeypatch.setattr(rd, "_ensure_catalog", AsyncMock())
    pd_pass = AsyncMock(return_value=(1, 1))
    monkeypatch.setattr(rd, "_reconcile_pd", pd_pass)
    litellm = MagicMock()
    litellm.create_model = AsyncMock(return_value={"model_info": {"id": "agg-1"}})
    monkeypatch.setattr(rd, "LiteLLMClient", lambda: litellm)

    out = await rd.reconcile_once()
    assert out == {"polled": 2, "transitions": 2, "registered": 2}
    pd_pass.assert_awaited_once()
    assert pd_pass.await_args.args[3] is dep
    # the aggregated row still goes through the classic path (ingress api_base)
    assert agg.status == "Ready"
    assert litellm.create_model.await_args.kwargs["api_base"] == "https://agg.example.com"
