"""Two-mode team hiding: default (discovery-only) vs strict (members too)."""

import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient


def _client(user, mock_litellm, mock_db) -> AsyncClient:
    """Client with BOTH dbs mocked — conftest's client_for_user leaves
    get_litellm_db real, which these list endpoints need mocked."""
    from app.auth.deps import get_current_user
    from app.clients.litellm import get_litellm_client
    from app.db.session import get_db, get_litellm_db
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_litellm_client] = lambda: mock_litellm
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_litellm_db] = lambda: mock_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _clear_overrides():
    from app.main import app

    app.dependency_overrides.clear()


class _R:
    """Fake DB result: mappings() is iterable and has first()."""

    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        rows = self._rows

        class _M:
            def __iter__(self):
                return iter(rows)

            def first(self):
                return rows[0] if rows else None

        return _M()


def _settings_rows(base=None, strict=None):
    rows = []
    if base is not None:
        rows.append({"key": "hidden_teams", "value": json.dumps(base)})
    if strict is not None:
        rows.append({"key": "hidden_teams_strict", "value": json.dumps(strict)})
    return _R(rows)


def _team_row(team_id, members=("user001",)):
    return {
        "team_id": team_id,
        "team_alias": team_id.title(),
        "max_budget": 100.0,
        "spend": 0.0,
        "budget_duration": None,
        "budget_reset_at": None,
        "models": [],
        "members": list(members),
        "admins": ["admin001"],
    }


@pytest.mark.asyncio
async def test_member_keeps_default_hidden_team_in_my_teams(regular_user, mock_litellm, mock_db):
    mock_db.execute = AsyncMock(
        side_effect=[
            _R([_team_row("team-1"), _team_row("team-2")]),  # my teams
            _settings_rows(base=["team-1"]),                 # hidden settings
            _R([]),                                          # descriptions
        ]
    )
    client = _client(regular_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/teams")
    finally:
        _clear_overrides()
    assert resp.status_code == 200, resp.text
    ids = [t["team_id"] for t in resp.json()["teams"]]
    assert ids == ["team-1", "team-2"]  # default hiding does NOT remove it here


@pytest.mark.asyncio
async def test_strict_hidden_team_gone_from_my_teams(regular_user, mock_litellm, mock_db):
    mock_db.execute = AsyncMock(
        side_effect=[
            _R([_team_row("team-1"), _team_row("team-2")]),
            _settings_rows(strict=["team-1"]),
            _R([]),
        ]
    )
    client = _client(regular_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/teams")
    finally:
        _clear_overrides()
    assert resp.status_code == 200, resp.text
    ids = [t["team_id"] for t in resp.json()["teams"]]
    assert ids == ["team-2"]


@pytest.mark.asyncio
async def test_discovery_hides_both_modes(regular_user, mock_litellm, mock_db):
    mock_db.execute = AsyncMock(
        side_effect=[
            _R([_team_row("team-1"), _team_row("team-2"), _team_row("team-3")]),  # all teams
            _R([]),                                                               # user teams
            _R([]),                                                               # pending joins
            _settings_rows(base=["team-1"], strict=["team-2"]),                   # hidden settings
            _R([]),                                                               # descriptions
        ]
    )
    client = _client(regular_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/teams/discover")
    finally:
        _clear_overrides()
    assert resp.status_code == 200, resp.text
    ids = [t["team_id"] for t in resp.json()["teams"]]
    assert ids == ["team-3"]


def _key_row(team_id):
    return {
        "token": f"tok-{team_id}",
        "key_name": f"sk-...{team_id}",
        "metadata": {},
        "team_id": team_id,
        "user_id": "user001",
        "spend": 0.0,
        "max_budget": None,
        "budget_duration": None,
        "budget_reset_at": None,
        "models": [],
        "expires": None,
        "created_at": None,
        "tpm_limit": None,
        "rpm_limit": None,
        "team_metadata": {},
    }


@pytest.mark.asyncio
async def test_keys_filter_only_strict_hidden(regular_user, mock_litellm, mock_db):
    mock_db.execute = AsyncMock(
        side_effect=[
            _R([_key_row("team-1"), _key_row("team-2")]),        # keys
            _settings_rows(base=["team-1"], strict=["team-2"]),  # hidden settings
        ]
    )
    client = _client(regular_user, mock_litellm, mock_db)
    try:
        resp = await client.get("/api/keys")
    finally:
        _clear_overrides()
    assert resp.status_code == 200, resp.text
    ids = [k["team_id"] for k in resp.json()["keys"]]
    assert ids == ["team-1"]  # default-hidden stays; strict-hidden gone


@pytest.mark.asyncio
async def test_get_hidden_teams_returns_both_lists(admin_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock(
        return_value=_settings_rows(base=["team-1"], strict=["team-2"])
    )
    resp = await admin_client.get("/api/settings/hidden-teams")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hidden_teams": ["team-1"], "hidden_teams_strict": ["team-2"]}


@pytest.mark.asyncio
async def test_update_hidden_teams_strict_wins_on_overlap(admin_client: AsyncClient, mock_db):
    mock_db.execute = AsyncMock()
    mock_db.commit = AsyncMock()
    resp = await admin_client.put(
        "/api/settings/hidden-teams",
        json={"hidden_teams": ["team-a", "team-b"], "hidden_teams_strict": ["team-b"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hidden_teams": ["team-a"], "hidden_teams_strict": ["team-b"]}
    # both settings keys upserted
    payloads = [c.args[1]["value"] for c in mock_db.execute.await_args_list]
    assert json.dumps(["team-a"]) in payloads
    assert json.dumps(["team-b"]) in payloads
