"""P/D serving through the recipe / deployment / benchmark / overview APIs (mock_db pattern)."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.model_deployments import _serialize
from app.api.serving_overview import build_overview
from app.services.llmd_manifests import PD_EPP_CONFIG_FILE


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


_RECIPE = {
    "name": "glm-pd",
    "model_path": "/models/glm",
    "image": "vllm/vllm-openai:latest",
    "gpu_count": 8,
    "engine_args": {"tensor-parallel-size": 8},
    "serving_mode": "pd",
    "pd_config": {"prefill": {"replicas": 1}, "decode": {"replicas": 2, "engine_args": {"max-num-seqs": 512}}},
    "runtime": {"shm_size_gi": 16, "host_ipc": True},
}


def _dep(**kw):
    base = dict(
        id=uuid.uuid4(),
        model_name="glm",
        cluster_id=None,
        namespace="default",
        image="vllm/vllm-openai:latest",
        replicas=1,
        gpu_count=8,
        gpu_resource_key="nvidia.com/gpu",
        cpu_request=None,
        cpu_limit=None,
        memory_request=None,
        memory_limit=None,
        node_selector=None,
        tolerations=None,
        pvc_name=None,
        pvc_mount_path=None,
        model_path="/models/glm",
        vllm_extra_args=None,
        env=None,
        engine="vllm",
        engine_args={"tensor-parallel-size": 8},
        probes=None,
        gpu_type=None,
        serving_mode="pd",
        pd_config={"prefill": {"replicas": 1}, "decode": {"replicas": 2}, "nixl_port": 5600},
        runtime=None,
        pd_status={"prefill": {"ready": 1, "desired": 1}, "decode": {"ready": 2, "desired": 2}},
        router_stack_id=uuid.uuid4(),
        router_stack_created=True,
        ingress_host="glm.example.com",
        ingress_path="/",
        ingress_class="nginx",
        status="Ready",
        status_message=None,
        ready_replicas=2,
        service_cluster_ip=None,
        litellm_model_id=None,
        last_synced_at=None,
        created_by=None,
        updated_by=None,
        created_at=None,
        updated_at=None,
        recipe_id=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _stack(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="glm-router",
        argo_app_name="llmd-glm-router",
        namespace="default",
        cluster_id=None,
        ingress_host="glm.example.com",
        values_snapshot={},
        target_model_name="glm",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ─── recipes ──────────────────────────────────────────────────────────────────


async def test_recipe_pd_create_normalises_pd_config(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_RECIPE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["serving_mode"] == "pd"
    assert body["pd_config"]["nixl_port"] == 5600 and body["pd_config"]["decode"]["replicas"] == 2
    assert body["pd_config"]["router"]["peak_prefill_throughput"] == 33821
    assert body["runtime"] == {"shm_size_gi": 16, "host_ipc": True}


async def test_recipe_pd_requires_vllm(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post(
            "/api/admin/serving-recipes", json={**_RECIPE, "engine": "sglang", "engine_args": {"tp-size": 8}}
        )
    assert resp.status_code == 422 and "requires engine 'vllm'" in resp.text


async def test_recipe_pd_rejects_portal_managed_flags(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post(
            "/api/admin/serving-recipes", json={**_RECIPE, "vllm_extra_args": ["--kv-transfer-config", "{}"]}
        )
    assert resp.status_code == 422 and "managed by the portal" in resp.text


async def test_recipe_aggregated_drops_pd_config(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={**_RECIPE, "serving_mode": "aggregated"})
    assert resp.status_code == 201
    assert resp.json()["serving_mode"] == "aggregated" and resp.json()["pd_config"] is None


# ─── deployments ──────────────────────────────────────────────────────────────


_DEPLOY = {
    "model_name": "glm",
    "model_path": "/models/glm",
    "image": "vllm/vllm-openai:latest",
    "gpu_count": 8,
    "ingress_host": "glm.example.com",
    "serving_mode": "pd",
    "pd_config": {"prefill": {"replicas": 1}, "decode": {"replicas": 2}, "router": {"peak_prefill_throughput": 5000}},
}


def _deploy_env(mock_db, *, existing_stack=None):
    mock_db.execute = AsyncMock(return_value=_result(scalar=existing_stack))
    mock_db.refresh = AsyncMock()
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    patches = [
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(None, "gpu-type"))),
    ]
    return k8s, patches


async def test_deploy_pd_applies_two_pools_and_creates_router(client_for_user, super_user, mock_db):
    k8s, patches = _deploy_env(mock_db)
    stack = _stack()
    create_stack = AsyncMock(return_value=stack)
    with patches[0], patches[1], patch("app.api.model_deployments.llmd_stacks.create_stack", create_stack):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=_DEPLOY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["serving_mode"] == "pd"
    assert body["router_stack_id"] == str(stack.id) and body["router_stack_created"] is True
    _ns, manifests = k8s.create_or_patch.await_args.args
    kinds = [(m["kind"], m["metadata"]["name"]) for m in manifests]
    assert kinds == [
        ("Deployment", "glm-prefill-deployment"),
        ("Deployment", "glm-decode-deployment"),
        ("Service", "glm-prefill-service"),
        ("Service", "glm-decode-service"),
    ]
    decode = manifests[1]["spec"]["template"]["spec"]
    assert decode["initContainers"][0]["name"] == "routing-proxy"
    kw = create_stack.await_args.kwargs
    assert kw["name"] == "glm-router" and kw["target_model_name"] == "glm" and kw["ingress_host"] == "glm.example.com"
    epp = kw["values"]["router"]["epp"]
    assert epp["pluginsConfigFile"] == PD_EPP_CONFIG_FILE
    assert "peakPrefillThroughput: 5000" in epp["pluginsCustomConfig"][PD_EPP_CONFIG_FILE]


async def test_deploy_pd_links_given_router_stack(client_for_user, super_user, mock_db):
    k8s, patches = _deploy_env(mock_db)
    stack = _stack()
    mock_db.get = AsyncMock(return_value=stack)
    create_stack = AsyncMock()
    with patches[0], patches[1], patch("app.api.model_deployments.llmd_stacks.create_stack", create_stack):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json={**_DEPLOY, "router_stack_id": str(stack.id)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["router_stack_id"] == str(stack.id) and resp.json()["router_stack_created"] is False
    create_stack.assert_not_awaited()


async def test_deploy_pd_reuses_existing_named_router(client_for_user, super_user, mock_db):
    stack = _stack()
    # the uniqueness query (deployment) must find nothing; the stack-by-name query finds the stack
    results = iter([_result(scalar=None), _result(scalar=stack)])
    k8s, patches = _deploy_env(mock_db)
    mock_db.execute = AsyncMock(side_effect=lambda *a, **k: next(results))
    create_stack = AsyncMock()
    with patches[0], patches[1], patch("app.api.model_deployments.llmd_stacks.create_stack", create_stack):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=_DEPLOY)
    assert resp.status_code == 201, resp.text
    assert resp.json()["router_stack_id"] == str(stack.id) and resp.json()["router_stack_created"] is False
    create_stack.assert_not_awaited()


async def test_deploy_pd_router_failure_is_reported_not_fatal(client_for_user, super_user, mock_db):
    k8s, patches = _deploy_env(mock_db)
    create_stack = AsyncMock(side_effect=HTTPException(status_code=502, detail="ArgoCD apply failed: RBAC"))
    with patches[0], patches[1], patch("app.api.model_deployments.llmd_stacks.create_stack", create_stack):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=_DEPLOY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["router_stack_id"] is None and body["router_stack_created"] is False
    assert "glm-router" in body["status_message"] and "RBAC" in body["status_message"]
    k8s.create_or_patch.assert_awaited_once()  # pools were still applied


async def test_deploy_pd_rejects_sglang(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments", json={**_DEPLOY, "engine": "sglang"})
    assert resp.status_code == 422


async def test_update_pd_validates_pd_config_against_row(client_for_user, super_user, mock_db):
    dep = _dep()
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    mock_db.refresh = AsyncMock()
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            bad = await client.put(
                f"/api/model-deployments/{dep.id}",
                json={"pd_config": {"decode": {"vllm_extra_args": ["--port=9"]}}},
            )
            ok = await client.put(f"/api/model-deployments/{dep.id}", json={"pd_config": {"decode": {"replicas": 4}}})
    assert bad.status_code == 400 and "managed by the portal" in bad.text
    assert ok.status_code == 200, ok.text
    assert ok.json()["pd_config"]["decode"]["replicas"] == 4
    _ns, manifests = k8s.create_or_patch.await_args.args
    assert manifests[1]["spec"]["replicas"] == 4 and len(manifests) == 4


async def test_get_pd_exposes_status_and_router(client_for_user, super_user, mock_db):
    dep = _dep()
    stack = _stack(id=dep.router_stack_id)
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep, all_rows=[]))
    mock_db.get = AsyncMock(return_value=stack)
    live = AsyncMock(return_value={"sync_status": "Synced", "health_status": "Healthy", "status_message": None})
    with patch("app.api.model_deployments.llmd_stacks.live_status", live):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{dep.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pd_summary"] == "D 2/2 · P 1/1"
    assert body["pd_status"]["decode"]["ready"] == 2
    assert body["router_stack"] == {
        "id": str(stack.id),
        "name": "glm-router",
        "namespace": "default",
        "ingress_host": "glm.example.com",
        "created_by_deployment": True,
        "sync_status": "Synced",
        "health_status": "Healthy",
        "status_message": None,
    }


async def test_delete_pd_removes_both_pools_and_auto_created_router(client_for_user, super_user, mock_db):
    dep = _dep()
    stack = _stack(id=dep.router_stack_id)
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    mock_db.get = AsyncMock(return_value=stack)
    k8s = MagicMock()
    k8s.delete = AsyncMock()
    delete_stack = AsyncMock()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.llmd_stacks.delete_stack", delete_stack),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/model-deployments/{dep.id}")
    assert resp.status_code == 200
    _ns, names = k8s.delete.await_args.args
    assert names == {
        "prefill_deployment": "glm-prefill-deployment",
        "decode_deployment": "glm-decode-deployment",
        "prefill_service": "glm-prefill-service",
        "decode_service": "glm-decode-service",
    }
    delete_stack.assert_awaited_once()
    assert delete_stack.await_args.args[1] is stack
    mock_db.delete.assert_awaited_once_with(dep)


async def test_delete_pd_keeps_router_it_did_not_create(client_for_user, super_user, mock_db):
    dep = _dep(router_stack_created=False)
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    k8s = MagicMock()
    k8s.delete = AsyncMock()
    delete_stack = AsyncMock()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.llmd_stacks.delete_stack", delete_stack),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.delete(f"/api/model-deployments/{dep.id}")
    assert resp.status_code == 200
    delete_stack.assert_not_awaited()


def test_serialize_pd_fields():
    out = _serialize(_dep())
    assert out["serving_mode"] == "pd" and out["pd_summary"] == "D 2/2 · P 1/1"
    assert out["router_stack_created"] is True and out["router_stack_id"]
    agg = _serialize(
        _dep(
            serving_mode="aggregated", pd_config=None, pd_status=None, router_stack_id=None, router_stack_created=False
        )
    )
    assert agg["serving_mode"] == "aggregated" and agg["pd_summary"] is None and agg["router_stack_id"] is None


# ─── overview ─────────────────────────────────────────────────────────────────


def test_overview_reports_pd_summary():
    dep = _dep()
    row = build_overview([dep], [], [], [], [])["models"][0]
    d = row["deployments"][0]
    assert d["serving_mode"] == "pd" and d["pd_summary"] == "D 2/2 · P 1/1"
    assert d["router_stack_id"] == str(dep.router_stack_id)


# ─── benchmarks ───────────────────────────────────────────────────────────────


async def test_benchmark_pd_targets_the_router_service(client_for_user, super_user, mock_db):
    dep = _dep()
    stack = _stack(id=dep.router_stack_id)
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    mock_db.get = AsyncMock(return_value=stack)
    mock_db.refresh = AsyncMock()
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    body = {"tool": "vllm_serving", "params": {"num_prompts": 10}, "deployment_id": str(dep.id)}
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 201, resp.text
    _ns, manifest = k8s.create_job.await_args.args
    import json

    text = json.dumps(manifest)
    assert "http://llmd-glm-router-epp.default.svc.cluster.local:8081" in text
    assert "glm-decode-service" not in text and "glm-prefill-service" not in text
    run = mock_db.add.call_args.args[0]
    assert run.serving_snapshot["serving_mode"] == "pd"


async def test_benchmark_pd_without_router_409(client_for_user, super_user, mock_db):
    dep = _dep(router_stack_id=None)
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    body = {"tool": "vllm_serving", "params": {}, "deployment_id": str(dep.id)}
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 409 and "router" in resp.text
    k8s.create_job.assert_not_awaited()


async def test_benchmark_ephemeral_clone_of_pd_400(client_for_user, super_user, mock_db):
    dep = _dep()
    mock_db.execute = AsyncMock(return_value=_result(scalar=dep))
    k8s = MagicMock()
    k8s.create_job = AsyncMock()
    body = {"tool": "vllm_serving", "params": {}, "ephemeral": True, "deployment_id": str(dep.id)}
    with patch("app.api.benchmarks.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/benchmarks", json=body)
    assert resp.status_code == 400 and "P/D" in resp.text
    k8s.create_job.assert_not_awaited()


async def test_deploy_pd_router_block_becomes_stack_overrides(client_for_user, super_user, mock_db):
    k8s, patches = _deploy_env(mock_db)
    stack = _stack()
    create_stack = AsyncMock(return_value=stack)
    body = {
        **_DEPLOY,
        "ingress_class": "nginx",
        "pd_config": {
            **_DEPLOY["pd_config"],
            "router": {"epp_tag": "v0.9.1", "epp_replicas": 2, "ingress_class": "internal"},
        },
    }
    with patches[0], patches[1], patch("app.api.model_deployments.llmd_stacks.create_stack", create_stack):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=body)
    assert resp.status_code == 201, resp.text
    kw = create_stack.await_args.kwargs
    assert kw["epp_tag"] == "v0.9.1" and kw["epp_registry"] is None and kw["ingress_class"] == "internal"
    epp = kw["values"]["router"]["epp"]
    assert epp["replicas"] == 2 and epp["image"]["tag"] == "v0.9.1"
