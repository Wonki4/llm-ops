"""Re-apply a recipe to a deployment; rollout guard on create/update."""

import uuid
from unittest.mock import AsyncMock, patch

from app.db.models.custom_model_deployment import CustomModelDeployment
from tests.test_storage_recipes_deployments import DEPLOY, _k8s, _phases, _refresh, _resolved, _result

RECIPE_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")


def _row(**kw):
    base = dict(
        id=uuid.uuid4(), model_name="llama", litellm_model_name=None, cluster_id=None, namespace="ns",
        recipe_id=RECIPE_ID, image="vllm/vllm-openai:v0.9", replicas=2, gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector={"gpu": "a100"}, tolerations=None, storages=[], model_path="/models/llama",
        vllm_extra_args=["--max-model-len", "8192"], env=None, engine="vllm", engine_args=None, probes=None,
        gpu_type=None, serving_mode="aggregated", pd_config=None, runtime=None, pd_status=None,
        router_stack_id=None, router_stack_created=False, ingress_host="h", ingress_path="/",
        ingress_class="nginx", status="Ready", status_message=None, ready_replicas=2, service_cluster_ip=None,
        litellm_model_id=None, last_synced_at=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return CustomModelDeployment(**base)


def _patches(k8s):
    return (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({}),
        patch("app.api.model_deployments.resolve_gpu_type", AsyncMock(return_value=(None, None))),
    )


# ─── rollout guard ────────────────────────────────────────────────────────────


async def test_create_rejects_replace_with_one_replica(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    k8s = _k8s()
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.post(
                "/api/model-deployments", json={**DEPLOY, "replicas": 1, "runtime": {"rollout": "replace"}}
            )
    assert resp.status_code == 400 and "needs at least 2 replicas" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_update_rejects_scaling_a_replace_rollout_to_one(client_for_user, super_user, mock_db):
    dep = _row(replicas=2, runtime={"rollout": "replace"})
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{dep.id}", json={"replicas": 1})
    assert resp.status_code == 400 and "needs at least 2 replicas" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_update_still_reapplies_manifests(client_for_user, super_user, mock_db):
    dep = _row()
    mock_db.execute = AsyncMock(return_value=_result(dep))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={})
    a, b, c, d = _patches(k8s)
    with a, b, c, d:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{dep.id}", json={"image": "vllm/vllm-openai:v0.10"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["image"] == "vllm/vllm-openai:v0.10" and resp.json()["status"] == "Updating"
    k8s.create_or_patch.assert_awaited_once()
