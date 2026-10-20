"""POST /api/admin/serving-recipes/preview: the manifests a deploy of an unsaved recipe would apply."""

from unittest.mock import AsyncMock, MagicMock, patch

import yaml

from tests._storage import SID

RECIPE = {"name": "llama", "model_path": "/models/llama", "image": "vllm/vllm-openai:v0.9",
          "storages": [{"storage_id": SID, "mount_path": "/models"}]}
POOL = {"image": "vllm/vllm-openai:v0.9", "model_path": "/models/llama", "gpu_count": 1, "replicas": 2,
        "storages": [{"storage_id": SID, "mount_path": "/models"}]}
PD = {**RECIPE, "serving_mode": "pd", "pd_config": {"prefill": POOL, "decode": {**POOL, "replicas": 1}}}
SNAP = {"binding_id": "b", "type": "pvc", "claim_name": "models-pvc", "namespace": "prod",
        "nfs_server": None, "nfs_path": None, "sub_path": None}
URL = "/api/admin/serving-recipes/preview"


def _result(scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    return r


def _bound(db, entries, *a, unresolved_out=None, **kw):
    return [{**e, "volume_name": "st-llama-weights", "resolved": SNAP} for e in entries or []]


def _unbound(db, entries, *a, unresolved_out=None, **kw):
    unresolved_out.append("llama-weights")
    return [{**e, "volume_name": "st-llama-weights",
             "resolved": {"binding_id": None, "type": "pvc", "claim_name": "<unbound:llama-weights>"}}
            for e in entries or []]


def _resolve(side_effect):
    return patch("app.services.storage_store.resolve_entries", AsyncMock(side_effect=side_effect))


def _docs(resp):
    return [yaml.safe_load(m["yaml"]) for m in resp.json()["manifests"]]


async def test_preview_aggregated_renders_deployment_service_ingress(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    with patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=_bound)) as res:
        async with client_for_user(super_user) as client:
            resp = await client.post(URL, json={**RECIPE, "namespace": "prod"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [(m["kind"], m["name"]) for m in data["manifests"]] == [
        ("Deployment", "llama-deployment"), ("Service", "llama-service"), ("Ingress", "llama-ingress"),
    ]
    assert data["warnings"] == []
    assert res.await_args.args[2:] == (None, "prod")
    dep = _docs(resp)[0]
    assert dep["metadata"]["namespace"] == "prod"
    assert dep["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"
    mock_db.add.assert_not_called()
    assert "&id" not in data["manifests"][0]["yaml"]  # shared label dicts are written out, not aliased


async def test_preview_pd_renders_both_pools_and_services(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    with (
        patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=_bound)),
        _resolve(_bound),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post(URL, json={**PD, "model_name": "llama-pd"})
    assert resp.status_code == 200, resp.text
    names = [(m["kind"], m["name"]) for m in resp.json()["manifests"]]
    assert names == [
        ("Deployment", "llama-pd-prefill-deployment"), ("Deployment", "llama-pd-decode-deployment"),
        ("Service", "llama-pd-prefill-service"), ("Service", "llama-pd-decode-service"),
    ]
    prefill, decode = _docs(resp)[:2]
    assert prefill["spec"]["replicas"] == 2 and decode["spec"]["replicas"] == 1
    for d in (prefill, decode):
        assert d["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"
    assert [c["name"] for c in decode["spec"]["template"]["spec"]["initContainers"]] == ["routing-proxy"]
    assert not prefill["spec"]["template"]["spec"].get("initContainers")


async def test_preview_unbound_storage_renders_placeholder_and_warns(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    with patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=_unbound)):
        async with client_for_user(super_user) as client:
            resp = await client.post(URL, json=RECIPE)
    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == ["storage_unresolved:llama-weights"]
    vol = _docs(resp)[0]["spec"]["template"]["spec"]["volumes"][0]
    assert vol["persistentVolumeClaim"]["claimName"] == "<unbound:llama-weights>"


async def test_preview_unknown_storage_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnknownStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    with patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=UnknownStorageError([SID]))):
        async with client_for_user(super_user) as client:
            resp = await client.post(URL, json=RECIPE)
    assert resp.status_code == 400 and SID in resp.text


async def test_preview_matches_what_deploy_applies_for_pd(client_for_user, super_user, mock_db):
    """Same recipe + target: the preview's manifests are exactly what create_deployment sends to K8s."""
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock()
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    deploy = {k: v for k, v in PD.items() if k not in ("name",)}
    with (
        patch("app.api.serving_recipes.resolve_entries", AsyncMock(side_effect=_bound)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_bound)),
        _resolve(_bound),
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments._attach_router", AsyncMock()),
        patch("app.api.model_deployments.pvc_phases", AsyncMock(return_value=None)),
    ):
        async with client_for_user(super_user) as client:
            preview = await client.post(URL, json={**PD, "namespace": "prod"})
            created = await client.post("/api/model-deployments", json={
                **deploy, "model_name": "llama", "namespace": "prod", "ingress_host": "llama.local", "replicas": 1,
            })
    assert preview.status_code == 200, preview.text
    assert created.status_code == 201, created.text
    assert _docs(preview) == k8s.create_or_patch.await_args.args[1]


async def test_preview_is_super_user_only(client_for_user, regular_user, mock_db):
    async with client_for_user(regular_user) as client:
        assert (await client.post(URL, json=RECIPE)).status_code == 403
