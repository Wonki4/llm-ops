"""Recipe and deployment APIs: storages in, legacy pvc fields out, resolution at deploy."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tests._storage import SID

NOW = datetime(2026, 10, 19, tzinfo=UTC)
RECIPE = {"name": "llama", "model_path": "/models/llama", "image": "vllm/vllm-openai:v0.9"}


def _result(scalar=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    return r


async def _refresh(row):
    row.created_at = row.updated_at = NOW


async def test_recipe_rejects_legacy_pvc_fields(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={**RECIPE, "pvc_name": "models-pvc"})
    assert resp.status_code == 422
    assert "replaced by storages" in resp.text


async def test_recipe_create_with_storages_and_warning(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    body = {**RECIPE, "model_path": "/data/llama", "storages": [{"storage_id": SID, "mount_path": "/models/"}]}
    with patch("app.api.serving_recipes.check_known", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["storages"] == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]
    assert data["warnings"] == ["model_path_outside_storages"]
    assert "pvc_name" not in data


async def test_recipe_unknown_storage_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnknownStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    body = {**RECIPE, "storages": [{"storage_id": SID, "mount_path": "/models"}]}
    with patch("app.api.serving_recipes.check_known", AsyncMock(side_effect=UnknownStorageError([SID]))):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 400 and SID in resp.text


async def test_recipe_rejects_overlapping_mounts(client_for_user, super_user, mock_db):
    body = {**RECIPE, "storages": [{"storage_id": SID, "mount_path": "/models"},
                                   {"storage_id": str(uuid.uuid4()), "mount_path": "/models/x"}]}
    async with client_for_user(super_user) as client:
        assert (await client.post("/api/admin/serving-recipes", json=body)).status_code == 422


PD = {**RECIPE, "serving_mode": "pd"}
MOUNT = [{"storage_id": SID, "mount_path": "/models"}]


async def test_recipe_rejects_legacy_pvc_in_pool(client_for_user, super_user, mock_db):
    body = {**PD, "pd_config": {"prefill": {"pvc_name": "models-pvc"}}}
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 422
    assert "replaced by storages" in resp.text


async def test_recipe_unknown_storage_in_pool_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnknownStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    body = {**PD, "pd_config": {"decode": {"storages": MOUNT}}}
    check = AsyncMock(side_effect=UnknownStorageError([SID]))
    with patch("app.api.serving_recipes.check_known", check):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 400 and SID in resp.text
    assert check.call_args.args[1] == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]


async def test_recipe_pool_model_path_outside_its_mounts_warns(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    body = {**PD, "storages": MOUNT,
            "pd_config": {"prefill": {"model_path": "/data/other", "storages": MOUNT}}}
    with patch("app.api.serving_recipes.check_known", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == ["model_path_outside_storages"]


async def test_recipe_empty_pool_storages_does_not_inherit_base(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    body = {**PD, "storages": MOUNT, "pd_config": {"decode": {"storages": []}}}
    with patch("app.api.serving_recipes.check_known", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/admin/serving-recipes", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["warnings"] == ["model_path_outside_storages"]


def _row(rid, **kw):
    import types

    base = dict(
        id=rid, name="llama", description=None, model_path="/models/llama", image="img", gpu_count=1,
        gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None, memory_request=None,
        memory_limit=None, node_selector=None, tolerations=None, storages=[], vllm_extra_args=None,
        env=None, engine="vllm", engine_args=None, created_by=None, updated_by=None,
        created_at=NOW, updated_at=NOW,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


async def test_recipe_update_unknown_storage_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnknownStorageError

    rid = uuid.uuid4()
    mock_db.execute = AsyncMock(side_effect=[_result(_row(rid)), _result(_row(rid))])
    body = {**RECIPE, "storages": MOUNT}
    with patch("app.api.serving_recipes.check_known", AsyncMock(side_effect=UnknownStorageError([SID]))):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/admin/serving-recipes/{rid}", json=body)
    assert resp.status_code == 400 and SID in resp.text


async def test_recipe_update_returns_storages_and_warnings(client_for_user, super_user, mock_db):
    rid = uuid.uuid4()
    mock_db.execute = AsyncMock(side_effect=[_result(_row(rid)), _result(_row(rid))])
    body = {**RECIPE, "model_path": "/data/llama", "storages": MOUNT}
    with patch("app.api.serving_recipes.check_known", AsyncMock()):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/admin/serving-recipes/{rid}", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["storages"] == [{"storage_id": SID, "mount_path": "/models", "read_only": True}]
    assert data["warnings"] == ["model_path_outside_storages"]


DEPLOY = {
    "model_name": "llama", "namespace": "prod", "model_path": "/models/llama", "ingress_host": "llama.local",
    "storages": [{"storage_id": SID, "mount_path": "/models"}],
}


def _k8s(live_labels: dict | None = None):
    k8s = MagicMock()
    k8s.create_or_patch = AsyncMock()
    k8s.read_deployment = AsyncMock(return_value={"labels": live_labels} if live_labels is not None else None)
    return k8s


def _resolved(db, entries, *a, **kw):
    """Stand-in for storage_store.resolve_entries(db, entries, cluster_id, namespace, *, previous)."""
    snap = {"binding_id": "b", "type": "pvc", "claim_name": "models-pvc", "namespace": "prod",
            "nfs_server": None, "nfs_path": None, "sub_path": None}
    return [{**e, "volume_name": "st-llama-weights", "resolved": snap} for e in entries]


def _phases(phases):
    return patch("app.api.model_deployments.pvc_phases", AsyncMock(return_value=phases))


async def test_deploy_snapshots_resolution_and_warns_pending_pvc(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)) as res,
        _phases({"models-pvc": "Pending"}) as phases,
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=DEPLOY)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["storages"][0]["resolved"]["claim_name"] == "models-pvc"
    assert data["warnings"] == ["pvc_pending:models-pvc"]
    assert res.await_args.args[2:] == (None, "prod")
    assert phases.await_args.args[1:] == (None, "prod")
    pod = next(m for m in k8s.create_or_patch.await_args.args[1] if m["kind"] == "Deployment")
    assert pod["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "models-pvc"


async def test_deploy_unresolved_storage_400_and_nothing_applied(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnresolvedStorageError

    mock_db.execute = AsyncMock(return_value=_result(None))
    k8s = _k8s()
    unresolved = AsyncMock(side_effect=UnresolvedStorageError(["llama-weights"]))
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", unresolved),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.post("/api/model-deployments", json=DEPLOY)
    assert resp.status_code == 400 and "llama-weights" in resp.text
    k8s.create_or_patch.assert_not_awaited()
    mock_db.add.assert_not_called()


async def test_deploy_rejects_legacy_pvc(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/model-deployments", json={**DEPLOY, "pvc_name": "x", "pvc_mount_path": "/m"})
    assert resp.status_code == 422


def _dep_row(**kw):
    from app.db.models.custom_model_deployment import CustomModelDeployment

    base = dict(
        id=uuid.uuid4(), model_name="llama", namespace="prod", cluster_id=None, image="img", replicas=1,
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", model_path="/models/llama", ingress_host="h",
        ingress_path="/", ingress_class="nginx", engine="vllm", serving_mode="aggregated", status="Running",
        ready_replicas=1, created_at=NOW, updated_at=NOW,
        storages=[{"storage_id": SID, "mount_path": "/models", "read_only": False, "volume_name": "model-weights",
                   "resolved": {"binding_id": "old"}}],
    )
    base.update(kw)
    return CustomModelDeployment(**base)


async def test_update_re_resolves_and_keeps_volume_name(client_for_user, super_user, mock_db):
    row = _dep_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=_k8s())),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)) as res,
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 200, resp.text
    entries, cluster, ns = res.await_args.args[1:4]
    assert entries[0]["storage_id"] == SID and (cluster, ns) == (None, "prod")
    assert res.await_args.kwargs["previous"][0]["volume_name"] == "model-weights"
    assert resp.json()["warnings"] == []


async def test_get_deployment_reports_stale(client_for_user, super_user, mock_db):
    row = _dep_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    with patch("app.api.model_deployments.stale_storage_ids", AsyncMock(return_value=[SID])):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{row.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["storage_stale"] == [SID]


def _pd_row():
    pool_storage = {"storage_id": SID, "mount_path": "/models", "read_only": True, "volume_name": "model-weights",
                    "resolved": {"binding_id": "old"}}
    return _dep_row(
        serving_mode="pd", storages=[],
        pd_config={"prefill": {"replicas": 1, "storages": [pool_storage]}, "decode": {"replicas": 1}},
    )


async def test_update_pd_pool_keeps_migrated_volume_name(client_for_user, super_user, mock_db):
    row = _pd_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    calls = []

    async def fake(db, entries, cluster, ns, *, previous=None, **kw):
        calls.append((entries, previous))
        return _resolved(db, entries)

    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=_k8s())),
        patch("app.services.storage_store.resolve_entries", AsyncMock(side_effect=fake)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=fake)),
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 200, resp.text
    pool_calls = [c for c in calls if c[1] and c[1][0].get("volume_name") == "model-weights"]
    assert pool_calls, calls
    assert pool_calls[-1][0][0]["storage_id"] == SID
    assert "resolved" not in pool_calls[-1][0][0]


async def test_update_pd_unresolved_pool_storage_400(client_for_user, super_user, mock_db):
    from app.services.storage_store import UnresolvedStorageError

    row = _pd_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    k8s = _k8s()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(return_value=[])),
        patch("app.services.storage_store.resolve_entries",
              AsyncMock(side_effect=UnresolvedStorageError(["llama-weights"]))),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 400 and "llama-weights" in resp.text
    k8s.create_or_patch.assert_not_awaited()


async def test_update_null_storages_means_unchanged(client_for_user, super_user, mock_db):
    row = _dep_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=_k8s())),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)) as res,
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"storages": None})
    assert resp.status_code == 200, resp.text
    assert res.await_args.args[1][0]["storage_id"] == SID
    assert len(resp.json()["storages"]) == 1


async def test_update_keeps_the_live_selector_owner_and_deploy_uses_the_new_one(client_for_user, super_user, mock_db):
    """Selectors are immutable: an update re-applies with the owner value the live Deployment
    was created with (pre-rename rows keep litellm-portal); a fresh deploy gets llm-ops-portal."""
    row = _dep_row()
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    k8s = _k8s(live_labels={"llm-ops/managed-by": "litellm-portal", "llm-ops/model-name": row.model_name})
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 200, resp.text
    applied = next(m for m in k8s.create_or_patch.await_args.args[1] if m["kind"] == "Deployment")
    assert applied["spec"]["selector"]["matchLabels"]["llm-ops/managed-by"] == "litellm-portal"

    mock_db.execute = AsyncMock(return_value=_result(None))
    fresh = _k8s()
    with (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=fresh)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({"models-pvc": "Bound"}),
    ):
        async with client_for_user(super_user) as client:
            created = await client.post("/api/model-deployments", json=DEPLOY)
    assert created.status_code == 201, created.text
    new = next(m for m in fresh.create_or_patch.await_args.args[1] if m["kind"] == "Deployment")
    assert new["spec"]["selector"]["matchLabels"]["llm-ops/managed-by"] == "llm-ops-portal"
