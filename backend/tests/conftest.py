"""Shared test fixtures for backend API tests."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.deps import get_current_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db
from app.main import app


# ─── Mock Users ───────────────────────────────────────────────


def _make_user(user_id: str = "user001", role: GlobalRole = GlobalRole.USER) -> CustomUser:
    user = CustomUser(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=f"Test {user_id}",
        global_role=role,
    )
    return user


@pytest.fixture
def regular_user() -> CustomUser:
    return _make_user("user001", GlobalRole.USER)


@pytest.fixture
def super_user() -> CustomUser:
    return _make_user("admin001", GlobalRole.SUPER_USER)


# ─── Mock LiteLLM Client ─────────────────────────────────────


@pytest.fixture
def mock_litellm() -> MagicMock:
    client = MagicMock(spec=LiteLLMClient)
    # Default returns for common methods
    client.get_user_info = AsyncMock(
        return_value={
            "user_info": {"user_id": "user001", "spend": 5.0, "max_budget": 100.0},
            "teams": [
                {
                    "team_id": "team-1",
                    "team_alias": "Alpha Team",
                    "max_budget": 500.0,
                    "spend": 120.0,
                    "budget_duration": "30d",
                    "budget_reset_at": "2026-04-01T00:00:00Z",
                    "models": ["gpt-4o", "claude-sonnet"],
                    "members": ["user001", "user002"],
                    "admins": ["admin001"],
                },
            ],
        }
    )
    client.create_user = AsyncMock(return_value={"user_id": "user001"})
    client.list_teams = AsyncMock(
        return_value=[
            {
                "team_id": "team-1",
                "team_alias": "Alpha Team",
                "max_budget": 500.0,
                "spend": 120.0,
                "models": ["gpt-4o", "claude-sonnet"],
                "members": ["user001", "user002"],
                "admins": ["admin001"],
            },
            {
                "team_id": "team-2",
                "team_alias": "Beta Team",
                "max_budget": 200.0,
                "spend": 50.0,
                "models": ["gpt-4o-mini"],
                "members": ["user002"],
                "admins": ["admin001"],
            },
        ]
    )
    client.get_team_info = AsyncMock(
        return_value={
            "team_info": {
                "team_id": "team-1",
                "team_alias": "Alpha Team",
                "max_budget": 500.0,
                "spend": 120.0,
                "budget_duration": "30d",
                "budget_reset_at": "2026-04-01T00:00:00Z",
                "models": ["gpt-4o", "claude-sonnet"],
                "members": ["user001", "user002"],
                "admins": ["admin001"],
            },
        }
    )
    client.list_keys = AsyncMock(
        return_value=[
            {
                "token": "sk-abc123xxxxxxxxxxxx",
                "key_alias": "user001-team-1",
                "team_id": "team-1",
                "user_id": "user001",
                "spend": 10.0,
                "max_budget": 50.0,
                "models": ["gpt-4o"],
                "created_at": "2026-01-15T10:00:00Z",
            },
        ]
    )
    client.generate_key = AsyncMock(
        return_value={
            "token": "sk-new-key-xxxxxxxxxxxx",
            "key_alias": "my-new-key",
            "team_id": "team-1",
            "user_id": "user001",
        }
    )
    client.get_key_info = AsyncMock(
        return_value={
            "info": {"user_id": "user001", "team_id": "team-1"},
        }
    )
    client.delete_key = AsyncMock(return_value={"deleted": True})
    client.add_team_member = AsyncMock(return_value={"status": "ok"})
    client.get_model_info = AsyncMock(
        return_value=[
            {"model_name": "gpt-4o", "litellm_params": {"model": "gpt-4o"}},
        ]
    )
    return client


# ─── Mock DB Session ──────────────────────────────────────────


@pytest.fixture
def mock_db() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


# ─── Test Client Factory ─────────────────────────────────────


@pytest.fixture
def client_for_user(mock_litellm: MagicMock, mock_db: AsyncMock):
    """Factory fixture: returns a function that creates a test client with a specific user."""

    def _make_client(user: CustomUser) -> AsyncClient:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_litellm_client] = lambda: mock_litellm
        app.dependency_overrides[get_db] = lambda: mock_db
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )

    yield _make_client
    app.dependency_overrides.clear()


@pytest.fixture
def user_client(client_for_user, regular_user) -> AsyncClient:
    """Pre-configured client authenticated as regular user."""
    return client_for_user(regular_user)


@pytest.fixture
def admin_client(client_for_user, super_user) -> AsyncClient:
    """Pre-configured client authenticated as super user."""
    return client_for_user(super_user)
