"""Tests for model catalog CRUD endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from app.api.models_catalog import _snapshot_default_cost_if_missing
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
            "input_cost_per_token": 0.005,
            "output_cost_per_token": 0.015,
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


@pytest.mark.asyncio
async def test_edit_default_prices(admin_client, mock_db):
    entry = CustomModelCatalog(id=uuid.uuid4(), model_name="m", status=ModelStatus.LTS)
    result = MagicMock()
    result.scalar_one_or_none.return_value = entry
    mock_db.execute.return_value = result
    prices = {"default_input_cost_per_token": 0, "default_output_cost_per_token": 1.25e-6}
    response = await admin_client.put(f"/api/models/catalog/{entry.id}", json=prices)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in prices.items())
    assert entry.default_cost_managed is True


@pytest.mark.asyncio
async def test_reject_negative_default_price(admin_client):
    response = await admin_client.put(
        f"/api/models/catalog/{uuid.uuid4()}", json={"default_output_cost_per_token": -1}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_follow_litellm_retains_baseline_for_schedules(admin_client, mock_db):
    entry = CustomModelCatalog(
        id=uuid.uuid4(), model_name="m", status=ModelStatus.LTS, default_cost_managed=True,
        default_input_cost_per_token=0, default_output_cost_per_token=2e-6,
    )
    mock_db.execute.return_value = MagicMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = entry
    url = f"/api/models/catalog/{entry.id}"
    response = await admin_client.put(url, json={"follow_litellm_pricing": True, "default_output_cost_per_token": 1})
    assert response.status_code == 422
    assert entry.default_cost_managed is True

    response = await admin_client.put(url, json={"follow_litellm_pricing": True})
    assert response.status_code == 200
    assert response.json()["default_cost_managed"] is False
    assert response.json()["default_input_cost_per_token"] == 0
    assert response.json()["default_output_cost_per_token"] == 2e-6


@pytest.mark.asyncio
async def test_snapshot_preserves_free_litellm_override(mock_db, mock_litellm):
    entry = CustomModelCatalog(model_name="m")
    mock_db.execute.return_value = MagicMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = entry
    mock_litellm.get_model_info.return_value = [{
        "model_name": "m",
        "litellm_params": {"input_cost_per_token": 0, "cache_read_input_token_cost": 0},
        "model_info": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6, "cache_read_input_token_cost": 3e-6},
    }]
    await _snapshot_default_cost_if_missing(mock_db, mock_litellm, "m")
    assert entry.default_input_cost_per_token == 0
    assert entry.default_output_cost_per_token == 2e-6
    assert entry.default_cache_read_cost_per_token == 0


@pytest.mark.asyncio
async def test_summary_finds_editable_deployment_for_shared_model_name(admin_client, mock_db, mock_litellm):
    name = "org/model"
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = result
    mock_litellm.get_model_info.return_value = [
        {"model_name": name, "model_info": {"id": "file", "db_model": False}},
        {"model_name": name, "model_info": {"id": "db", "db_model": True}},
    ]
    response = await admin_client.get("/api/models/org%2Fmodel/summary")
    assert response.status_code == 200
    assert response.json()["litellm_info"]["model_info"]["id"] == "db"
