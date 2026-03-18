"""Tests for GET /api/me endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_me_returns_user_profile(user_client: AsyncClient, mock_litellm):
    resp = await user_client.get("/api/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "user001"
    assert data["email"] == "user001@example.com"
    assert data["role"] == "user"
    assert isinstance(data["teams"], list)
    assert len(data["teams"]) == 1
    assert data["teams"][0]["team_alias"] == "Alpha Team"


@pytest.mark.asyncio
async def test_get_me_auto_provisions_litellm_user(user_client: AsyncClient, mock_litellm):
    """When LiteLLM user doesn't exist, auto-create and retry."""
    call_count = 0
    original_data = {
        "user_info": {"user_id": "user001", "spend": 0, "max_budget": None},
        "teams": [],
    }

    async def side_effect(user_id):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("User not found")
        return original_data

    mock_litellm.get_user_info.side_effect = side_effect
    resp = await user_client.get("/api/me")
    assert resp.status_code == 200
    assert mock_litellm.create_user.call_count == 1
    assert call_count == 2


@pytest.mark.asyncio
async def test_get_me_super_user(admin_client: AsyncClient, mock_litellm):
    mock_litellm.get_user_info.return_value = {
        "user_info": {"user_id": "admin001", "spend": 0},
        "teams": [],
    }
    resp = await admin_client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "super_user"
