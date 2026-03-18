"""Tests for API key management endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_key(user_client: AsyncClient, mock_litellm):
    resp = await user_client.post(
        "/api/keys",
        json={
            "team_id": "team-1",
            "key_alias": "my-test-key",
            "max_budget": 25.0,
            "budget_duration": "30d",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    mock_litellm.generate_key.assert_called_once()
    call_kwargs = mock_litellm.generate_key.call_args
    assert call_kwargs.kwargs["user_id"] == "user001"
    assert call_kwargs.kwargs["team_id"] == "team-1"


@pytest.mark.asyncio
async def test_create_key_default_alias(user_client: AsyncClient, mock_litellm):
    """When no key_alias provided, default to user_id-team_id."""
    resp = await user_client.post("/api/keys", json={"team_id": "team-1"})
    assert resp.status_code == 200
    call_kwargs = mock_litellm.generate_key.call_args
    assert call_kwargs.kwargs["key_alias"] == "user001-team-1"


@pytest.mark.asyncio
async def test_list_my_keys(user_client: AsyncClient, mock_litellm):
    resp = await user_client.get("/api/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert len(data["keys"]) == 1


@pytest.mark.asyncio
async def test_list_keys_with_team_filter(user_client: AsyncClient, mock_litellm):
    resp = await user_client.get("/api/keys?team_id=team-1")
    assert resp.status_code == 200
    mock_litellm.list_keys.assert_called_with(user_id="user001", team_id="team-1")


@pytest.mark.asyncio
async def test_delete_own_key(user_client: AsyncClient, mock_litellm):
    resp = await user_client.delete("/api/keys/sk-abc123")
    assert resp.status_code == 200
    mock_litellm.delete_key.assert_called_once_with("sk-abc123")


@pytest.mark.asyncio
async def test_delete_other_users_key_forbidden(user_client: AsyncClient, mock_litellm):
    """Cannot delete a key owned by another user."""
    mock_litellm.get_key_info.return_value = {
        "info": {"user_id": "other-user", "team_id": "team-1"},
    }
    resp = await user_client.delete("/api/keys/sk-other-key")
    assert resp.status_code == 403
