"""Deploy namespace: validated up front, defaulted from the cluster, clear errors when K8s refuses it."""

import json
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from app.services.clusters import default_namespace, validate_namespace
from tests.test_storage_recipes_deployments import DEPLOY, _k8s, _phases, _refresh, _resolved, _result

BODY = {k: v for k, v in DEPLOY.items() if k not in ("namespace", "storages")}


@pytest.mark.parametrize("ok", ["default", "team-a", "a", "llm-ops-01", "a" * 63])
def test_valid_namespaces(ok):
    assert validate_namespace(f"  {ok} ") == ok


@pytest.mark.parametrize("bad", ["Bad_NS", "Team-A", "-a", "a-", "a.b", "a" * 64, "한글"])
def test_invalid_namespaces(bad):
    with pytest.raises(ValueError, match="not a valid Kubernetes name"):
        validate_namespace(bad)


def test_blank_namespace_means_default():
    assert validate_namespace("  ") is None and validate_namespace(None) is None


async def test_default_namespace_is_the_clusters_else_the_portal_setting():
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(types.SimpleNamespace(namespace="gpu-serving")))
    assert await default_namespace(db, uuid.uuid4()) == "gpu-serving"
    db.execute = AsyncMock(return_value=_result(None))  # unknown cluster id
    with patch("app.config.settings.k8s_default_namespace", "llm"):
        assert await default_namespace(db, uuid.uuid4()) == "llm"
        assert await default_namespace(db, None) == "llm"


def _deploy_patches(k8s):
    return (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({}),
    )


async def test_invalid_namespace_422_and_nothing_applied(client_for_user, super_user, mock_db):
    k8s = _k8s()
    a, b, c = _deploy_patches(k8s)
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json={**BODY, "namespace": "Bad_NS"})
    assert resp.status_code == 422 and "not a valid Kubernetes name" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_omitted_namespace_uses_the_cluster_default(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    a, b, c = _deploy_patches(k8s)
    with a, b, c, patch("app.api.model_deployments.default_namespace", AsyncMock(return_value="gpu-serving")):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json={**BODY, "namespace": " "})
    assert resp.status_code == 201, resp.text
    assert resp.json()["namespace"] == "gpu-serving"
    assert k8s.create_or_patch.await_args.args[0] == "gpu-serving"


def _api_error(status, details):
    e = ApiException(status=status, reason="x")
    e.body = json.dumps({"kind": "Status", "reason": "NotFound", "details": details})
    return e


@pytest.mark.parametrize(
    ("error", "code", "text"),
    [
        (_api_error(404, {"name": "team-b", "kind": "namespaces"}), 400, "Namespace 'team-b' does not exist"),
        (_api_error(403, {}), 502, "not allowed to create resources in namespace 'team-b'"),
        (_api_error(404, {"name": "x", "kind": "deployments"}), 502, "Failed to apply K8s resources"),
        (RuntimeError("boom"), 502, "Failed to apply K8s resources"),
    ],
)
async def test_apply_errors(client_for_user, super_user, mock_db, error, code, text):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    k8s.create_or_patch = AsyncMock(side_effect=error)
    a, b, c = _deploy_patches(k8s)
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json={**BODY, "namespace": "team-b"})
    assert resp.status_code == code and text in resp.text


async def test_preview_validates_and_defaults_the_namespace(client_for_user, super_user, mock_db):
    recipe = {"name": "llama", "model_path": "/models/llama", "image": "img"}
    async with client_for_user(super_user) as client:
        bad = await client.post("/api/admin/serving-recipes/preview", json={**recipe, "namespace": "Team_A"})
    assert bad.status_code == 422
    with (
        patch("app.api.serving_recipes.default_namespace", AsyncMock(return_value="gpu-serving")),
        patch("app.api.model_deployments.pvc_index", AsyncMock(return_value=None)),
    ):
        async with client_for_user(super_user) as client:
            ok = await client.post("/api/admin/serving-recipes/preview", json={**recipe, "namespace": ""})
    assert ok.status_code == 200, ok.text
    dep = next(m for m in ok.json()["manifests"] if m["kind"] == "Deployment")
    assert "namespace: gpu-serving" in dep["yaml"]


async def test_cluster_list_carries_the_portal_default_namespace(client_for_user, super_user, mock_db):
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=rows)
    with patch("app.api.k8s_clusters.settings.k8s_default_namespace", "llm"):
        async with client_for_user(super_user) as client:
            resp = await client.get("/api/admin/k8s-clusters")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"clusters": [], "default_namespace": "llm"}
