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
