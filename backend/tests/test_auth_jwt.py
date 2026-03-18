import pytest
from jose import JWTError

from app.auth import jwt as jwt_module


@pytest.mark.asyncio
async def test_verify_token_uses_preferred_username_when_sub_missing(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_jwks() -> dict:
        return {"keys": []}

    payload = {
        "preferred_username": "user001",
        "email": "user001@example.com",
        "name": "User 001",
        "realm_access": {"roles": ["user"]},
        "resource_access": {"litellm-portal": {"roles": ["super_user"]}},
    }

    monkeypatch.setattr(jwt_module, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(jwt_module.jwt, "decode", lambda *args, **kwargs: payload)

    token = await jwt_module.verify_token("dummy")

    assert token.sub == "user001"
    assert token.preferred_username == "user001"
    assert token.email == "user001@example.com"
    assert token.realm_roles == ["user"]
    assert token.client_roles == ["super_user"]


@pytest.mark.asyncio
async def test_verify_token_raises_when_sub_and_preferred_username_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_get_jwks() -> dict:
        return {"keys": []}

    monkeypatch.setattr(jwt_module, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(jwt_module.jwt, "decode", lambda *args, **kwargs: {"email": "x@y.com"})

    with pytest.raises(ValueError, match="missing both 'sub' and 'preferred_username'"):
        await jwt_module.verify_token("dummy")


@pytest.mark.asyncio
async def test_verify_token_wraps_jwt_errors(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_jwks() -> dict:
        return {"keys": []}

    def raise_jwt_error(*args, **kwargs):
        raise JWTError("bad token")

    monkeypatch.setattr(jwt_module, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(jwt_module.jwt, "decode", raise_jwt_error)

    with pytest.raises(ValueError, match="Invalid token: bad token"):
        await jwt_module.verify_token("dummy")
