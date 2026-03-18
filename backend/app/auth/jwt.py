"""JWT verification using Keycloak JWKS endpoint."""

import time
from dataclasses import dataclass

import httpx
from jose import JWTError, jwt

from app.config import settings

_jwks_cache: dict | None = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # 1 hour


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_cache_time
    now = time.time()
    if _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache
    async with httpx.AsyncClient(verify=settings.ssl_verify) as client:
        resp = await client.get(settings.effective_jwks_uri)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_cache_time = now
        assert _jwks_cache is not None
        return _jwks_cache


@dataclass
class TokenPayload:
    sub: str  # Keycloak subject (unique user ID)
    preferred_username: str  # 사번
    email: str | None = None
    name: str | None = None
    realm_roles: list[str] | None = None
    client_roles: list[str] | None = None


async def verify_token(token: str) -> TokenPayload:
    """Verify and decode a Keycloak JWT access token."""
    try:
        jwks = await _get_jwks()
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.keycloak_issuer,
            options={"verify_at_hash": False},
        )
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e

    subject = payload.get("sub")
    preferred_username = payload.get("preferred_username")
    if not subject and not preferred_username:
        raise ValueError("Invalid token: missing both 'sub' and 'preferred_username' claims")

    if not subject:
        subject = preferred_username
    if not preferred_username:
        preferred_username = subject

    assert subject is not None
    assert preferred_username is not None

    # Extract roles from realm_access and resource_access
    realm_roles = payload.get("realm_access", {}).get("roles", [])
    client_roles = payload.get("resource_access", {}).get(settings.jwt_audience, {}).get("roles", [])

    return TokenPayload(
        sub=subject,
        preferred_username=preferred_username,
        email=payload.get("email"),
        name=payload.get("name"),
        realm_roles=realm_roles,
        client_roles=client_roles,
    )
