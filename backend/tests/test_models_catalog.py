"""Tests for model catalog CRUD endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_models_merges_catalog_and_litellm(user_client: AsyncClient, mock_litellm, mock_db):
    """Models list merges LiteLLM runtime info with custom catalog entries."""
    # Mock catalog query result
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    resp = await user_client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) == 1
    assert data["models"][0]["model_name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_list_catalog_requires_super_user(user_client: AsyncClient):
    """Regular users cannot access the catalog management endpoint."""
    resp = await user_client.get("/api/models/catalog")
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
    mock_db.add.assert_called_once()


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
