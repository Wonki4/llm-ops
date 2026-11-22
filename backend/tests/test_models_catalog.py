"""Tests for model catalog CRUD endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.db.models.custom_model_catalog import CustomModelCatalog, ModelStatus
from app.db.models.custom_model_status_history import CustomModelStatusHistory


@pytest.mark.asyncio
async def test_list_models_merges_catalog_and_litellm(user_client: AsyncClient, mock_litellm, mock_db):
    """Models list merges LiteLLM runtime info with custom catalog entries."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        CustomModelCatalog(model_name="gpt-4o", status=ModelStatus.LTS, visible=True)
    ]
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await user_client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) == 1
    assert data["models"][0]["model_name"] == "gpt-4o"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"default_input_cost_per_token": 0},
    {"follow_litellm_pricing": True},
])
async def test_edit_catalog_pricing_requires_super_user(user_client: AsyncClient, body):
    resp = await user_client.put(f"/api/models/catalog/{uuid.uuid4()}", json=body)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_catalog_super_user(admin_client: AsyncClient, mock_db):
    """Super users can list catalog entries."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await admin_client.get("/api/models/catalog")
    assert resp.status_code == 200
    assert "catalog" in resp.json()


@pytest.mark.asyncio
async def test_create_catalog_entry_requires_super_user(user_client: AsyncClient):
    """Regular users cannot create catalog entries."""
    resp = await user_client.post(
        "/api/models/catalog",
        json={
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_catalog_entry(admin_client: AsyncClient, mock_db):
    """Super user can create a catalog entry."""
    # Mock: no existing entry with same model_name
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await admin_client.post(
        "/api/models/catalog",
        json={
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
            "description": "OpenAI GPT-4o model",
            "status": "lts",
        },
    )
    assert resp.status_code == 201
    entry, history = (call.args[0] for call in mock_db.add.call_args_list)
    assert isinstance(entry, CustomModelCatalog)
    assert isinstance(history, CustomModelStatusHistory)
    assert history.catalog_id == entry.id
    assert history.new_status == entry.status


@pytest.mark.asyncio
async def test_create_catalog_duplicate_rejected(admin_client: AsyncClient, mock_db):
    """Duplicate model_name returns 409."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock()  # existing entry
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await admin_client.post(
        "/api/models/catalog",
        json={
            "model_name": "gpt-4o",
            "display_name": "GPT-4o",
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_catalog_requires_super_user(user_client: AsyncClient):
    """Regular users cannot delete catalog entries."""
    resp = await user_client.delete("/api/models/catalog/some-id")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_catalog_not_found(admin_client: AsyncClient, mock_db):
    """Deleting a non-existent entry returns 404."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await admin_client.delete("/api/models/catalog/00000000-0000-0000-0000-000000000001")
    assert resp.status_code == 404


@pytest.mark.parametrize("body", [
    {"default_input_cost_per_token": 0},
    {"default_output_cost_per_token": 2e-6},
    {"default_cache_read_cost_per_token": 1e-6},
    {"follow_litellm_pricing": True},
])
async def test_catalog_base_price_is_read_only_even_for_admin(admin_client, mock_db, body):
    response = await admin_client.put(f"/api/models/catalog/{uuid.uuid4()}", json=body)
    assert response.status_code == 422
    response = await admin_client.post("/api/models/catalog", json={"model_name": "m", "display_name": "M", **body})
    assert response.status_code == 422
    mock_db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_finds_editable_deployment_for_shared_model_name(admin_client, mock_db, mock_litellm):
    name = "org/model"
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = result
    mock_litellm.get_model_info.return_value = [
        {"model_name": name, "model_info": {"id": "file", "db_model": False}},
        {"model_name": name, "model_info": {"id": "db", "db_model": True, "input_cost_per_token": 2e-6},
         "litellm_params": {"input_cost_per_token": 0, "cache_read_input_token_cost": 0}},
        {"model_name": name, "model_info": {"id": "second", "db_model": True, "input_cost_per_token": 3e-6}},
    ]
    response = await admin_client.get("/api/models/org%2Fmodel/summary")
    assert response.status_code == 200
    assert response.json()["litellm_info"]["model_info"]["id"] == "db"
    prices = response.json()["deployment_prices"]
    assert [p["id"] for p in prices] == ["file", "db", "second"]
    assert [p["input_cost_per_token"] for p in prices] == [None, 0, 3e-6]
    assert prices[1]["cache_read_input_token_cost"] == 0


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/catalog"),
    ("POST", "/api/catalog"),
    ("GET", "/api/catalog/catalogs"),
    ("PUT", "/api/catalog/catalogs"),
    ("PUT", "/api/catalog/entry/model"),
    ("DELETE", "/api/catalog/entry/model"),
    ("PATCH", "/api/catalog/entry/model/apikey"),
    ("GET", "/api/models/org/model/cache"),
    ("PUT", "/api/models/org/model/cache/chat"),
    ("DELETE", "/api/models/org/model/cache/chat"),
])
async def test_removed_redis_catalog_endpoints_are_unavailable(admin_client, method, path):
    response = await admin_client.request(method, path)
    assert response.status_code == 404
