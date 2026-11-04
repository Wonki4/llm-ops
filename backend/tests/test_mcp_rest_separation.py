"""The portal REST API must not accept access tokens issued to the MCP client.

Keycloak's `roles` scope adds `litellm-portal` to `aud` for any user holding a
portal client role, so an MCP token can pass the portal's audience check; the
REST Bearer branch tells them apart by `azp`.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.auth import deps
from app.auth.jwt import TokenPayload


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/me", "headers": []})


def _patch(monkeypatch, *, session=None, payload: TokenPayload):
    monkeypatch.setattr(deps, "refresh_session_if_needed", AsyncMock(return_value=session))
    verify = AsyncMock(return_value=payload)
    monkeypatch.setattr(deps, "verify_token", verify)
    return verify


async def test_bearer_token_issued_to_mcp_client_is_rejected(monkeypatch):
    # The shape Keycloak issues to the MCP client for a user with a portal client role:
    # aud=["litellm-portal", "portal-mcp"] passes verify_token, azp gives it away.
    _patch(monkeypatch, payload=TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp"))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="x")

    with pytest.raises(HTTPException) as e:
        await deps.get_current_token(_request(), Response(), creds)

    assert e.value.status_code == 401


async def test_bearer_token_from_other_client_still_accepted(monkeypatch):
    payload = TokenPayload(sub="s", preferred_username="admin001", azp="litellm-portal")
    _patch(monkeypatch, payload=payload)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="x")

    assert await deps.get_current_token(_request(), Response(), creds) is payload


async def test_session_cookie_token_is_not_subject_to_the_mcp_rule(monkeypatch):
    payload = TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp")
    verify = _patch(monkeypatch, session=SimpleNamespace(access_token="cookie"), payload=payload)

    assert await deps.get_current_token(_request(), Response(), None) is payload
    verify.assert_awaited_once_with("cookie")
