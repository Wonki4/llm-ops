"""GET /api/model-deployments/external/recipe-draft and recipe_id on deployments."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.model_deployments import _serialize
from app.clients.k8s import K8sNotConfigured


def _live_spec():
    return {
        "name": "ext-vllm", "namespace": "team-a", "labels": {}, "replicas": 1,
        "container": {
            "name": "s", "image": "vllm/vllm-openai:v0.6.0", "command": [],
            "args": ["--model", "/models/llama", "--port", "8080", "--tensor-parallel-size", "2"],
            "env": [], "env_raw": [], "resources": {"limits": {"nvidia.com/gpu": "2"}}, "ports": [],
            "volume_mounts": [],
        },
        "volumes": [], "node_selector": None, "tolerations": None,
    }


def _k8s(read_result):
    k8s = MagicMock()
    k8s.read_deployment = AsyncMock(return_value=read_result) if not isinstance(read_result, Exception) \
        else AsyncMock(side_effect=read_result)
    return k8s


def _no_rows():
    r = MagicMock()
    r.scalars.return_value.all.return_value = []
    r.scalar_one_or_none.return_value = None
    return r


async def test_recipe_draft_parses_live_deployment(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_no_rows())  # GPU profile lookup after parsing
    k8s = _k8s(_live_spec())
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get(
                "/api/model-deployments/external/recipe-draft",
                params={"namespace": "team-a", "deployment_name": "ext-vllm"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["draft"]["model_path"] == "/models/llama"
    assert body["draft"]["engine_args"] == {"tensor-parallel-size": 2}
    assert body["draft"]["gpu_count"] == 2
    assert {"code": "port_changed", "detail": "8080 → 8000"} in body["warnings"]
    assert body["source"]["deployment_name"] == "ext-vllm"
    k8s.read_deployment.assert_awaited_once_with("team-a", "ext-vllm")


async def test_recipe_draft_404_when_deployment_gone(client_for_user, super_user, mock_db):
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=_k8s(None))):
        async with client_for_user(super_user) as client:
            resp = await client.get(
                "/api/model-deployments/external/recipe-draft",
                params={"namespace": "team-a", "deployment_name": "gone"},
            )
    assert resp.status_code == 404


async def test_recipe_draft_503_without_kubeconfig(client_for_user, super_user, mock_db):
    k8s = _k8s(K8sNotConfigured("no kubeconfig"))
    with patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)):
        async with client_for_user(super_user) as client:
            resp = await client.get(
                "/api/model-deployments/external/recipe-draft",
                params={"namespace": "team-a", "deployment_name": "x"},
            )
    assert resp.status_code == 503


async def test_recipe_draft_requires_query_params(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/model-deployments/external/recipe-draft")
    assert resp.status_code == 422


def test_serialize_exposes_recipe_link():
    rid = uuid.uuid4()
    dep = MagicMock(recipe_id=rid, cluster_id=None, last_synced_at=None, created_at=None, updated_at=None)
    out = _serialize(dep, {rid: "llama-tp2"})
    assert out["recipe_id"] == str(rid) and out["recipe_name"] == "llama-tp2"
    dep.recipe_id = None
    out = _serialize(dep)
    assert out["recipe_id"] is None and out["recipe_name"] is None
