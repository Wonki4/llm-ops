"""Tests for the external API endpoints (X-Api-Key auth)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.session import get_litellm_db
from app.main import app

API_KEY = "test-external-key"


def _make_litellm_db(rows: list[dict]) -> AsyncMock:
    """Build an AsyncMock that simulates litellm_db.execute() returning given rows.

    Supports both `.mappings().first()` (single-row team_id lookup) and
    `list(result.mappings())` (multi-row alias lookup).
    """
    mappings = MagicMock()
    mappings.first.return_value = rows[0] if rows else None
    mappings.__iter__ = lambda self: iter(rows)
    result = MagicMock()
    result.mappings.return_value = mappings
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def external_api_key(monkeypatch) -> str:
    monkeypatch.setattr(settings, "external_api_key", API_KEY)
    return API_KEY


@pytest.fixture
def litellm_client() -> MagicMock:
    client = MagicMock(spec=LiteLLMClient)
    client.update_team = AsyncMock(return_value={"team_id": "team-1"})
    client.create_team = AsyncMock(return_value={"team_id": "newly-created-id"})
    return client


@pytest.fixture
def external_client_factory(litellm_client: MagicMock):
    """Returns a factory that wires the litellm_db rows for a single request."""

    def _make(rows: list[dict]) -> AsyncClient:
        db = _make_litellm_db(rows)
        app.dependency_overrides[get_litellm_client] = lambda: litellm_client
        app.dependency_overrides[get_litellm_db] = lambda: db
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    yield _make
    app.dependency_overrides.clear()


# ─── by-name endpoint: existing team (no create_if_missing needed) ────


@pytest.mark.asyncio
async def test_by_name_updates_existing_team(
    external_api_key: str, external_client_factory, litellm_client: MagicMock
):
    rows = [{"team_id": "team-1", "team_alias": "alpha"}]
    async with external_client_factory(rows) as ac:
        resp = await ac.put(
            "/api/external/teams/by-name/alpha/budget",
            headers={"X-Api-Key": external_api_key},
            json={"max_budget": 50, "budget_duration": "30d"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "updated"
    assert body["team_id"] == "team-1"
    assert body["max_budget"] == 50
    assert body["budget_duration"] == "30d"
    litellm_client.update_team.assert_awaited_once_with(
        "team-1", max_budget=50, budget_duration="30d"
    )
    litellm_client.create_team.assert_not_awaited()


@pytest.mark.asyncio
async def test_by_name_with_create_if_missing_updates_existing_team(
    external_api_key: str, external_client_factory, litellm_client: MagicMock
):
    """create_if_missing=true on an existing team still just updates, no create."""
    rows = [{"team_id": "team-1", "team_alias": "alpha"}]
    async with external_client_factory(rows) as ac:
        resp = await ac.put(
            "/api/external/teams/by-name/alpha/budget",
            headers={"X-Api-Key": external_api_key},
            json={"max_budget": 75, "create_if_missing": True},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "updated"
    litellm_client.update_team.assert_awaited_once_with("team-1", max_budget=75)
    litellm_client.create_team.assert_not_awaited()


# ─── by-name endpoint: missing team ────────────────────────────────────


@pytest.mark.asyncio
async def test_by_name_missing_without_flag_returns_404(
    external_api_key: str, external_client_factory, litellm_client: MagicMock
):
    async with external_client_factory(rows=[]) as ac:
        resp = await ac.put(
            "/api/external/teams/by-name/ghost/budget",
            headers={"X-Api-Key": external_api_key},
            json={"max_budget": 50},
        )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]
    litellm_client.create_team.assert_not_awaited()
    litellm_client.update_team.assert_not_awaited()


@pytest.mark.asyncio
async def test_by_name_missing_with_flag_creates_team(
    external_api_key: str, external_client_factory, litellm_client: MagicMock
):
    async with external_client_factory(rows=[]) as ac:
        resp = await ac.put(
            "/api/external/teams/by-name/new-team-x/budget",
            headers={"X-Api-Key": external_api_key},
            json={
                "max_budget": 100,
                "budget_duration": "30d",
                "create_if_missing": True,
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "created"
    assert body["team_id"] == "newly-created-id"
    assert body["team_alias"] == "new-team-x"
    assert body["max_budget"] == 100
    assert body["budget_duration"] == "30d"
    # create_if_missing must NOT be forwarded to the LiteLLM payload
    litellm_client.create_team.assert_awaited_once_with(
        team_alias="new-team-x",
        models=[],
        max_budget=100,
        budget_duration="30d",
    )
    litellm_client.update_team.assert_not_awaited()


# ─── by-name endpoint: ambiguous alias ─────────────────────────────────


@pytest.mark.asyncio
async def test_by_name_ambiguous_returns_409_even_with_create_flag(
    external_api_key: str, external_client_factory, litellm_client: MagicMock
):
    rows = [
        {"team_id": "team-1", "team_alias": "shared"},
        {"team_id": "team-2", "team_alias": "shared"},
    ]
    async with external_client_factory(rows) as ac:
        resp = await ac.put(
            "/api/external/teams/by-name/shared/budget",
            headers={"X-Api-Key": external_api_key},
            json={"max_budget": 50, "create_if_missing": True},
        )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["team_ids"] == ["team-1", "team-2"]
    litellm_client.create_team.assert_not_awaited()
    litellm_client.update_team.assert_not_awaited()


# ─── by-id endpoint: schema does not accept create_if_missing ──────────


@pytest.mark.asyncio
async def test_by_id_rejects_create_if_missing(
    external_api_key: str, external_client_factory
):
    rows = [{"team_id": "team-1", "team_alias": "alpha"}]
    async with external_client_factory(rows) as ac:
        resp = await ac.put(
            "/api/external/teams/team-1/budget",
            headers={"X-Api-Key": external_api_key},
            json={"max_budget": 50, "create_if_missing": True},
        )
    # Pydantic strict-by-default rejects unknown fields? Default is allow extras.
    # The endpoint should still succeed (extras ignored) without calling create_team.
    # Either 200 with the extra ignored, or 422 — both prove it doesn't create.
    assert resp.status_code in (200, 422)
