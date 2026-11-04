"""Portal MCP: Keycloak token verification and the super-user gate."""

import dataclasses
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk
from jose import jwt as jose_jwt
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

from app.auth import jwt as jwt_module
from app.auth.jwt import TokenPayload
from app.config import settings
from app.mcp import auth as mcp_auth
from app.services.user_provisioning import IdentityMismatchError

MCP_PAYLOAD = TokenPayload(sub="s", preferred_username="admin001", azp="portal-mcp", exp=1_900_000_000)


def _fake_verify(monkeypatch, *, payload: TokenPayload | None = None, exc: Exception | None = None) -> dict:
    seen: dict = {}

    async def fake(token: str, audience: str | None = None) -> TokenPayload:
        seen["token"], seen["audience"] = token, audience
        if exc:
            raise exc
        assert payload is not None
        return payload

    monkeypatch.setattr(mcp_auth, "verify_token", fake)
    return seen


@contextmanager
def _as_caller(payload: TokenPayload):
    access = AccessToken(token="t", client_id="portal-mcp", scopes=[], claims=dataclasses.asdict(payload))
    reset = auth_context_var.set(AuthenticatedUser(access))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


# ─── verifier ─────────────────────────────────────────────────


async def test_valid_mcp_token_maps_to_access_token(monkeypatch):
    seen = _fake_verify(monkeypatch, payload=MCP_PAYLOAD)

    result = await mcp_auth.McpTokenVerifier().verify_token("t")

    assert result is not None
    assert result.client_id == "portal-mcp"
    assert result.expires_at == 1_900_000_000
    assert result.subject == "s"
    assert result.claims["preferred_username"] == "admin001"
    assert seen == {"token": "t", "audience": settings.mcp_token_audience}


async def test_token_for_other_client_is_rejected(monkeypatch):
    # e.g. a portal login token that also carries the MCP audience
    _fake_verify(monkeypatch, payload=dataclasses.replace(MCP_PAYLOAD, azp="litellm-portal"))

    assert await mcp_auth.McpTokenVerifier().verify_token("t") is None


async def test_invalid_token_is_rejected(monkeypatch):
    # bad signature, wrong issuer, expired, missing MCP audience: verify_token raises ValueError
    _fake_verify(monkeypatch, exc=ValueError("Invalid token: Signature has expired."))

    assert await mcp_auth.McpTokenVerifier().verify_token("t") is None


async def test_realistic_dual_audience_token_is_accepted(monkeypatch):
    # What Keycloak issues to the MCP client for a user holding a litellm-portal client role:
    # aud=["litellm-portal", "portal-mcp"] (checked inside verify_token), azp="portal-mcp",
    # and the portal client roles that make the user a super user.
    payload = dataclasses.replace(MCP_PAYLOAD, client_roles=["super_user"], realm_roles=["offline_access"])
    _fake_verify(monkeypatch, payload=payload)

    result = await mcp_auth.McpTokenVerifier().verify_token("t")

    assert result is not None
    assert result.claims["client_roles"] == ["super_user"]


# ─── caller identity ──────────────────────────────────────────


async def test_super_user_passes(monkeypatch, mock_db, super_user):
    upsert = AsyncMock(return_value=super_user)
    monkeypatch.setattr(mcp_auth, "upsert_portal_user", upsert)

    with _as_caller(MCP_PAYLOAD):
        assert await mcp_auth.require_mcp_super_user(mock_db) is super_user

    upsert.assert_awaited_once_with(mock_db, MCP_PAYLOAD)


async def test_regular_user_gets_super_user_only(monkeypatch, mock_db, regular_user):
    monkeypatch.setattr(mcp_auth, "upsert_portal_user", AsyncMock(return_value=regular_user))

    with _as_caller(MCP_PAYLOAD), pytest.raises(ToolError) as e:
        await mcp_auth.require_mcp_super_user(mock_db)

    assert str(e.value) == "super user only"


async def test_identity_mismatch_message(monkeypatch, mock_db):
    monkeypatch.setattr(mcp_auth, "upsert_portal_user", AsyncMock(side_effect=IdentityMismatchError("ADMIN001")))

    with _as_caller(MCP_PAYLOAD), pytest.raises(ToolError) as e:
        await mcp_auth.require_mcp_super_user(mock_db)

    assert str(e.value) == "계정 확인이 필요합니다. 관리자에게 문의하세요."


async def test_no_token_in_context_is_unauthenticated(mock_db):
    with pytest.raises(ToolError) as e:
        await mcp_auth.require_mcp_super_user(mock_db)

    assert str(e.value) == "unauthenticated"


# ─── real signatures (python-jose audience handling) ──────────


@pytest.fixture
def signed(monkeypatch):
    """Sign RS256 tokens with a throwaway key served as the realm's JWKS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_jwk = {**jwk.construct(public_pem, "RS256").to_dict(), "kid": "k1", "use": "sig"}

    async def fake_get_jwks() -> dict:
        return {"keys": [public_jwk]}

    monkeypatch.setattr(jwt_module, "_get_jwks", fake_get_jwks)

    def sign(**claims) -> str:
        base = {
            "iss": settings.keycloak_issuer,
            "sub": "s",
            "preferred_username": "admin001",
            "exp": int(time.time()) + 300,
        }
        return jose_jwt.encode({**base, **claims}, private_pem, algorithm="RS256", headers={"kid": "k1"})

    return sign


async def test_signed_dual_audience_mcp_token_is_accepted(signed):
    token = signed(aud=["litellm-portal", "portal-mcp"], azp="portal-mcp")

    result = await mcp_auth.McpTokenVerifier().verify_token(token)

    assert result is not None and result.client_id == "portal-mcp"


async def test_signed_portal_login_token_is_rejected(signed):
    token = signed(aud="litellm-portal", azp="litellm-portal")

    assert await mcp_auth.McpTokenVerifier().verify_token(token) is None


async def test_signed_expired_mcp_token_is_rejected(signed):
    token = signed(aud=["litellm-portal", "portal-mcp"], azp="portal-mcp", exp=1)

    assert await mcp_auth.McpTokenVerifier().verify_token(token) is None
