"""Exercise recovery against simulated Kubernetes rollout/Pod state and LiteLLM HTTP."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.api import model_deployments as deployments
from app.api import serving_recipes as recipes
from app.clients.litellm import LiteLLMClient
from app.mcp import serving, tools
from app.services import serving_diagnostics
from tests.test_deployment_reapply import _recipe
from tests.test_storage_recipes_deployments import _refresh, _result


async def test_recover_decode_using_the_same_deployment_and_probe_its_revision(mock_db, super_user, monkeypatch):
    recipe = _recipe(
        runtime=None,
        vllm_extra_args=None,
        serving_mode="pd",
        pd_config={
            "prefill": {"engine_args": {"max-num-seqs": 123}},
            "decode": {"engine_args": {"invalid-serving-flag": True}, "replicas": 2},
        },
    )

    @asynccontextmanager
    async def session():
        yield mock_db

    monkeypatch.setattr(tools, "async_session_factory", session)
    monkeypatch.setattr(tools, "require_mcp_super_user", AsyncMock(return_value=super_user))
    mock_db.commit = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=recipe)
    mock_db.get = AsyncMock(return_value=recipe)
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_db.execute = AsyncMock(return_value=_result(None))
    monkeypatch.setattr(recipes, "_by_id", AsyncMock(return_value=recipe))
    monkeypatch.setattr(recipes, "_by_name", AsyncMock(return_value=recipe))
    monkeypatch.setattr(deployments, "resolve_gpu_type", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(deployments, "load_deployment_copy", AsyncMock(return_value={}))
    monkeypatch.setattr(deployments, "_attach_router", AsyncMock())
    router = {"sync_status": "Synced", "health_status": "Healthy"}
    monkeypatch.setattr(deployments, "_router_stack_summary", AsyncMock(return_value=router))

    specs, states = {}, {}

    async def apply(namespace, manifests):
        rollout = {}
        for m in manifests:
            if m["kind"] != "Deployment":
                continue
            name, spec = m["metadata"]["name"], m["spec"]
            gen = states.get(name, {}).get("generation", 0) + (specs.get(name) != spec)
            specs[name] = spec
            bad = "--invalid-serving-flag" in str(spec["template"]["spec"]["containers"])
            count = spec["replicas"]
            states[name] = dict(
                uid=name,
                generation=gen,
                observed_generation=gen,
                desired=count,
                total=count,
                updated=count,
                available=0 if bad else count,
                ready=0 if bad else count,
            )
            rollout[name] = {"uid": name, "generation": gen}
        return rollout

    k8s = AsyncMock()
    k8s.create_or_patch.side_effect = apply
    k8s.read_deployment.return_value = {"labels": {}}
    k8s.read_deployment_status.side_effect = lambda ns, name: states[name]
    for module in (deployments, serving):
        monkeypatch.setattr(module, "k8s_for_cluster", AsyncMock(return_value=k8s))

    created = await tools.deploy_from_recipe(
        str(recipe.id),
        serving.DeployTarget(
            model_name="glm",
            ingress_host="glm.example.test",
            namespace="test",
            prefill_replicas=3,
        ),
    )
    dep = mock_db.add.call_args_list[0].args[0]
    sibling_creation = await tools.deploy_from_recipe(
        str(recipe.id),
        serving.DeployTarget(model_name="sibling", ingress_host="sibling.test", namespace="test"),
    )
    sibling = mock_db.add.call_args_list[-1].args[0]
    rows = {dep.id: dep, sibling.id: sibling}
    mock_db.execute.side_effect = lambda stmt: _result(rows.get(stmt.compile().params.get("id_1")))
    sibling_id = sibling_creation["id"]
    sibling_revision = {n: serving.Revision(**v) for n, v in sibling_creation["rollout"].items()}
    sibling_specs = {n: deepcopy(specs[n]) for n in sibling_revision}
    assert dep.recipe_id == sibling.recipe_id == recipe.id
    attach_count = deployments._attach_router.await_count
    did = created["id"]
    initial = {n: serving.Revision(**v) for n, v in created["rollout"].items()}
    status = await tools.get_rollout_status(did, initial)
    assert status["pools"]["prefill"]["status"] == "Ready"
    assert status["pools"]["decode"]["status"] == "Pending" and not status["ready"]

    # Kubernetes returns an exited previous container's stderr while the current pod backs off.
    from kubernetes_asyncio import client

    core = AsyncMock()
    prior = client.V1ContainerStateTerminated(exit_code=2, reason="Error")
    cs = client.V1ContainerStatus(
        name="vllm",
        ready=False,
        restart_count=1,
        image="v",
        image_id="img",
        state=client.V1ContainerState(waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff")),
        last_state=client.V1ContainerState(terminated=prior),
    )
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name="decode-pod", uid="pod-id"),
        status=client.V1PodStatus(phase="Running", container_statuses=[cs]),
    )
    core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
    core.list_namespaced_event.return_value = SimpleNamespace(items=[])
    core.read_namespaced_pod_log.side_effect = lambda *a, previous, **kw: (
        "unknown --invalid-serving-flag" if previous else ""
    )
    apps = AsyncMock()
    apps.read_namespaced_deployment.return_value = SimpleNamespace(
        metadata=pod.metadata, spec=SimpleNamespace(selector=SimpleNamespace(match_labels={"pool": "decode"}))
    )
    monkeypatch.setattr(serving_diagnostics.client, "AppsV1Api", lambda api: apps)
    monkeypatch.setattr(serving_diagnostics.client, "CoreV1Api", lambda api: core)
    k8s._api_client.return_value = MagicMock()
    diag = await tools.get_deployment_diagnostics(did, "decode")
    assert diag["pods"][0]["containers"][0]["previous_log"] == "unknown --invalid-serving-flag"
    core.list_namespaced_pod.assert_awaited_once_with("test", label_selector="pool=decode", _request_timeout=10)

    patch = {"pd_config": {"decode": {"engine_args": {"invalid-serving-flag": None}}}}
    diff = await tools.preview_deployment_recipe_patch(did, patch)
    early_b = await tools.preview_deployment_recipe_patch(sibling_id, patch)
    assert diff["recipe"]["id"] == str(recipe.id) and set(diff["recipe_changes"]) == {"pd_config"}
    assert diff["rollout"]["blocked"] is None
    assert [m["name"] for m in diff["manifests"] if m["changed"]] == ["glm-dep-dc"]
    assert recipe.pd_config["decode"]["engine_args"]["invalid-serving-flag"]
    with pytest.raises(ToolError, match="409"):
        await tools.apply_deployment_recipe_patch(sibling_id, patch, diff["preview_id"])
    with pytest.raises(ToolError, match="409"):
        await tools.apply_deployment_recipe_patch(did, {"image": "different"}, diff["preview_id"])
    with pytest.raises(ToolError, match="Router stack"):
        await tools.preview_deployment_recipe_patch(did, {"pd_config": {"router": {"epp_replicas": 2}}})
    assert k8s.create_or_patch.await_count == 2

    reapplied = await tools.apply_deployment_recipe_patch(did, patch, diff["preview_id"])
    assert reapplied["id"] == did and reapplied["pd_config"]["prefill"]["replicas"] == 3
    assert dep.recipe_id == sibling.recipe_id == recipe.id
    assert recipe.pd_config["prefill"]["engine_args"] == {"max-num-seqs": 123}
    assert recipe.pd_config["prefill"]["replicas"] == 1  # A's scale is not copied to the recipe
    assert not recipe.pd_config["decode"].get("engine_args")
    assert sibling.pd_config["decode"]["engine_args"]["invalid-serving-flag"]
    assert {n: specs[n] for n in sibling_revision} == sibling_specs
    assert not (await tools.get_rollout_status(sibling_id, sibling_revision))["ready"]
    assert all(m["metadata"]["name"].startswith("glm-") for m in k8s.create_or_patch.await_args.args[1])
    assert deployments._attach_router.await_count == attach_count
    with pytest.raises(ToolError, match="409"):
        await tools.apply_deployment_recipe_patch(sibling_id, patch, early_b["preview_id"])
    # B adopts the shared edit only when explicitly reapplied.
    later_b = await tools.preview_deployment_recipe_patch(sibling_id, {})
    assert not later_b["recipe_changes"]
    upgraded_b = await tools.apply_deployment_recipe_patch(sibling_id, {}, later_b["preview_id"])
    assert upgraded_b["id"] == sibling_id and not sibling.pd_config["decode"].get("engine_args")
    assert all(m["metadata"]["name"].startswith("sibling-") for m in k8s.create_or_patch.await_args.args[1])
    current = {n: serving.Revision(**v) for n, v in reapplied["rollout"].items()}
    assert (await tools.get_rollout_status(did, initial))["pools"]["decode"]["status"] == "Superseded"
    states["glm-dep-dc"]["observed_generation"] -= 1
    assert not (await tools.get_rollout_status(did, current))["ready"]
    states["glm-dep-dc"]["observed_generation"] += 1
    router["health_status"] = "Progressing"
    assert not (await tools.smoke_test_deployment(did, current))["ok"]
    router["health_status"] = "Healthy"
    dep.litellm_model_id = "unique-deployment-id"

    def inference(request):
        import json

        assert json.loads(request.content)["model"] == dep.litellm_model_id
        return httpx.Response(
            200,
            headers={"x-litellm-model-id": dep.litellm_model_id},
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    monkeypatch.setattr(
        LiteLLMClient,
        "_client",
        lambda self: httpx.AsyncClient(base_url="http://proxy.test", transport=httpx.MockTransport(inference)),
    )
    result = await tools.smoke_test_deployment(did, current)
    assert result["ok"] and result["model_id"] == dep.litellm_model_id


async def test_smoke_rejects_a_success_served_by_a_different_model(monkeypatch):
    def fallback(request):
        return httpx.Response(
            200, headers={"x-litellm-model-id": "other"}, json={"choices": [{"message": {"content": "OK"}}]}
        )

    monkeypatch.setattr(
        LiteLLMClient,
        "_client",
        lambda self: httpx.AsyncClient(base_url="http://proxy.test", transport=httpx.MockTransport(fallback)),
    )
    assert not (await LiteLLMClient().smoke_chat("target"))["ok"]


def test_aggregated_recipe_keeps_placement_and_rejects_pool_overrides():
    recipe = recipes._serialize(_recipe())
    target = serving.DeployTarget(model_name="agg", ingress_host="agg.test", replicas=2)
    body = serving.deploy_body(recipe, target)
    assert body.replicas == 2 and body.serving_mode == "aggregated" and body.engine_args == recipe["engine_args"]
    with pytest.raises(ValueError, match="PD recipe"):
        serving.deploy_body(recipe, target.model_copy(update={"decode_replicas": 2}))
