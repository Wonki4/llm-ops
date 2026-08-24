"""Serving recipe CRUD API — mock_db pattern (mirrors test_llmd.py)."""

import types
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.api.serving_recipes import _serialize


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _recipe(**kw):
    base = dict(
        id=uuid.uuid4(), name="r1", description=None, model_path="/w/llama", image="vllm:latest",
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        pvc_name=None, pvc_mount_path=None, vllm_extra_args=["--tensor-parallel-size", "2"],
        env=None, created_by=None, updated_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


_BODY = {"name": "r1", "model_path": "/w/llama", "image": "vllm:latest",
         "vllm_extra_args": ["--tensor-parallel-size", "2"]}


def test_serialize_round_trips_fields():
    out = _serialize(_recipe(name="x", gpu_count=4))
    assert out["name"] == "x" and out["gpu_count"] == 4
    assert out["vllm_extra_args"] == ["--tensor-parallel-size", "2"]


async def test_create_recipe_201(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))  # name is free
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "r1" and body["model_path"] == "/w/llama"
    mock_db.add.assert_called_once()


async def test_create_duplicate_name_409(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_recipe()))  # name taken
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json=_BODY)
    assert resp.status_code == 409


async def test_create_missing_required_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post("/api/admin/serving-recipes", json={"name": "r1"})  # no model_path/image
    assert resp.status_code == 422


async def test_create_bad_env_type_422(client_for_user, super_user, mock_db):
    async with client_for_user(super_user) as client:
        resp = await client.post(
            "/api/admin/serving-recipes",
            json={**_BODY, "env": ["not", "a", "dict"]},
        )
    assert resp.status_code == 422


async def test_list_recipes(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_recipe(name="a"), _recipe(name="b")]))
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/admin/serving-recipes")
    assert resp.status_code == 200
    assert [r["name"] for r in resp.json()["recipes"]] == ["a", "b"]


async def test_get_missing_404(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))
    async with client_for_user(super_user) as client:
        resp = await client.get(f"/api/admin/serving-recipes/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_recipe(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(scalar=_recipe()))
    mock_db.delete = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.delete(f"/api/admin/serving-recipes/{uuid.uuid4()}")
    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once()
