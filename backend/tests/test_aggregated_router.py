"""The recipe owns defaults; each aggregated deployment owns its router and target port."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi import HTTPException

from app.api.model_deployments import CreateDeploymentRequest
from app.jobs.reconcile_deployments import _router_target
from app.services import aggregated_router, llmd_stacks, recipe_reapply
from app.services.llmd_manifests import llmd_service_name
from app.services.model_deployment_manifests import build_all
from tests.test_deployment_reapply import _recipe, _row
from tests.test_storage_recipes_deployments import DEPLOY


async def test_chart_and_images_match_editor_preview_and_application(client_for_user, super_user):
    from app.api.llmd import _serialize

    async with client_for_user(super_user) as client:
        endpoint = "/api/admin/serving-recipes"
        defaults = (await client.post(f"{endpoint}/router-defaults", json={"serving_mode": "aggregated"})).json()
        assert defaults["chart_version"] == "v1.0.0"
        router = yaml.safe_load(defaults["values_yaml"])["router"]
        assert router["epp"]["image"]["tag"] == "v1.0.0"
        assert router["proxy"]["presets"]["envoy"]["image"] == "docker.io/envoyproxy/envoy:distroless-v1.33.2"
        for proxy in ({"presets": {"envoy": {"image": "mirror/envoy:test"}}}, {"image": "mirror/envoy:test"}):
            cfg = {
                "chart_repo": "oci://mirror/charts", "chart_name": "standalone", "chart_version": "1.0.0-mirror",
                "values_yaml": yaml.safe_dump({"router": {"proxy": proxy, "epp": {"image": {"tag": "mirror"}}}}),
            }
            response = await client.post(f"{endpoint}/preview", json={
                **DEPLOY, "name": "preview", "image": "test", "storages": [], "aggregated_router": cfg,
            })
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview["router_images"] == {
                "envoy": "mirror/envoy:test", "epp": "ghcr.io/llm-d/llm-d-router-endpoint-picker:mirror",
            }
            stack = aggregated_router.stack_for(_row(aggregated_router=cfg))
            source = llmd_stacks.application_for(stack, "argocd", "cluster")["spec"]["source"]
            assert (source["repoURL"], source["chart"], source["targetRevision"]) == (
                "mirror/charts", "standalone", "1.0.0-mirror",
            )
            assert source["helm"]["valuesObject"]["router"]["proxy"] == stack.helm_values["router"]["proxy"]
            assert _serialize(stack, {})["epp_image"] == preview["router_images"]["epp"]


@pytest.mark.parametrize(
    "config,port", [(None, 8000), ({}, 8000), ({"values_yaml": "router: {epp: {replicas: 2}}"}, 8100)]
)
async def test_preview_matches_routing(client_for_user, super_user, config, port):
    dep = _row(aggregated_router=config, ingress_path="/tenant", engine_args={"port": port})
    resources = build_all(dep)
    async with client_for_user(super_user) as client:
        body = {
            **DEPLOY,
            "storages": [],
            "model_name": dep.model_name,
            "ingress_host": dep.ingress_host,
            "namespace": dep.namespace,
            "ingress_path": dep.ingress_path,
            "engine_args": dep.engine_args,
            "aggregated_router": config,
        }
        preview = await client.post("/api/model-deployments/registration-preview?include_ingress=true", json=body)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["api_base"] == "https://h/tenant/v1"
        assert data["routing"]["target_port"] == port
        assert data["routing"]["mode"] == ("llm-d" if config is not None else "direct")
        recipe = await client.post("/api/admin/serving-recipes/preview", json={**body, "name": "r", "image": "test"})
        assert recipe.status_code == 200, recipe.text
        rendered = {m["kind"]: yaml.safe_load(m["yaml"]) for m in recipe.json()["manifests"]}
    if config is not None:
        stack = aggregated_router.stack_for(dep)
        resources.append(llmd_stacks.ingress_for(stack))
        values = rendered["values.yaml"]["router"]
        assert values == stack.helm_values["router"]
        assert values["modelServers"] == {
            "type": "vllm",
            "matchLabels": {"llm-d.ai/model": dep.model_name},
            "targetPorts": [{"number": port}],
        }
        assert values["epp"]["replicas"] == (2 if port == 8100 else 1)
        assert data["ingress"]["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"][
            "name"
        ] == llmd_service_name(stack)
    assert [m for m in resources if m["kind"] == "Ingress"] == [data["ingress"]]
    assert rendered["Ingress"]["metadata"]["name"] == data["ingress"]["metadata"]["name"]


@pytest.mark.parametrize(
    "values",
    [
        {"modelServers": {"matchLabels": {"llm-d.ai/model": "other"}}},
        {"modelServers": {"targetPorts": [{"number": 8001}]}},
        {"epp": {"flags": {"endpoint-selector": "app=other"}}},
        {"proxy": {"mode": "service"}},
    ],
)
def test_conflicting_target_is_rejected(values):
    with pytest.raises(ValueError):
        CreateDeploymentRequest(**DEPLOY, aggregated_router={"values_yaml": yaml.safe_dump({"router": values})})


async def test_partial_apply_retries_same_stack_and_updates_only_a(monkeypatch):
    config = {"values_yaml": "router: {modelServers: {targetPorts: [{number: 8100}]}}"}
    a, b = (_row(model_name=name, aggregated_router=config, engine_args={"port": 8100}) for name in ("a", "b"))
    db = AsyncMock()
    db.scalar.return_value = None
    saved = {}
    db.add = MagicMock(side_effect=lambda stack: saved.update({stack.id: stack}))
    db.get.side_effect = lambda _, id: saved[id]
    apply = AsyncMock(side_effect=[HTTPException(502, "Argo unavailable"), None, None])
    monkeypatch.setattr(llmd_stacks, "apply_stack", apply)
    await aggregated_router.sync(db, a)
    stack_id = a.router_stack_id
    assert stack_id and await _router_target(db, a) == (None, "Argo unavailable")
    await aggregated_router.sync(db, b)
    b_values = saved[b.router_stack_id].helm_values
    a.engine_args = {"port": 8200}
    await aggregated_router.sync(db, a)
    a_proxy = saved[stack_id].helm_values["router"]["proxy"]
    b_proxy = b_values["router"]["proxy"]
    assert a_proxy["configMap"]["name"] != b_proxy["configMap"]["name"]
    for proxy in (a_proxy, b_proxy):
        assert proxy["volumes"][0]["configMap"]["name"] == proxy["configMap"]["name"]
    assert a.router_stack_id == stack_id != b.router_stack_id
    assert a.router_apply_error is None and len(saved) == 2
    assert saved[stack_id].helm_values["router"]["modelServers"]["targetPorts"] == [{"number": 8200}]
    assert b_values["router"]["modelServers"] == {
        "type": "vllm",
        "targetPorts": [{"number": 8100}],
        "matchLabels": {"llm-d.ai/model": "b"},
    }
    assert "aggregated_router" not in recipe_reapply.recipe_values(_recipe(aggregated_router=None), a)
    state = {"sync_status": "OutOfSync", "health_status": "Healthy", "status_message": "waiting for sync"}
    monkeypatch.setattr(llmd_stacks, "live_status", AsyncMock(return_value=state))
    base, reason = await _router_target(db, a)
    assert base is None and "waiting for sync" in reason
    state["sync_status"] = "Synced"
    assert await _router_target(db, a) == ("https://h", None)


async def test_recipe_router_is_inherited_when_omitted_and_snapshotted():
    from app.api.model_deployments import _inherit_recipe_router

    recipe = _recipe(aggregated_router={})
    db = AsyncMock()
    db.get.return_value = recipe
    body = CreateDeploymentRequest(**DEPLOY, recipe_id=str(recipe.id))
    a = await _inherit_recipe_router(db, body)
    recipe.aggregated_router = None
    b = await _inherit_recipe_router(db, body)
    assert a.aggregated_router == {}
    assert b.aggregated_router is None


async def test_dedicated_router_blocks_another_stack_owner_and_stale_health(monkeypatch):
    stack = aggregated_router.stack_for(_row(aggregated_router={}))
    db = AsyncMock()
    db.scalar.return_value = "owner-id"
    with pytest.raises(HTTPException) as caught:
        await llmd_stacks.require_independent(db, stack)
    assert caught.value.status_code == 409
    k8s = AsyncMock()
    monkeypatch.setattr(llmd_stacks, "argocd_placement_for", AsyncMock(return_value=(k8s, "argocd", "cluster")))
    sync = {"status": "Synced", "comparedTo": {"source": {"helm": {"valuesObject": {}}}}}
    k8s.get_application.return_value = {"status": {"sync": sync, "health": {"status": "Healthy"}}}
    assert (await llmd_stacks.live_status(db, stack))["sync_status"] == "OutOfSync"
    sync["comparedTo"]["source"]["helm"]["valuesObject"] = stack.values_snapshot
    assert (await llmd_stacks.live_status(db, stack))["sync_status"] == "Synced"
