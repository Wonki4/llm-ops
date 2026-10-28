"""Keycloak SSO: swap a user's Keycloak access token for their LiteLLM key.

Web apps that already sign users in with Keycloak forward the user's access
token as ``Authorization: Bearer <token>``. The gateway recognises it by its
``iss`` claim, asks the management backend (``/api/external/token-auth``) to
verify it and return the user's gateway SSO key, and forwards the request to
LiteLLM with that key instead. The Keycloak token never reaches LiteLLM.

Recognition only *peeks* at the unverified payload; the backend does the
signature/audience checks. Peeking at ``iss`` rather than "is it a JWT"
matters: portal-issued LiteLLM keys are themselves HS256 JWTs (shown to users
without the ``sk-`` prefix) and carry no ``iss``, so they keep taking the
normal key path.
"""

import base64
import hashlib
import json
import time

import httpx

from app.config import settings

# Cache: hash(token, team) -> (litellm_key, expires_at_monotonic).
_cache: dict[str, tuple[str, float]] = {}

_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0)


class SsoAuthError(Exception):
    """The SSO token could not be exchanged; ``status_code`` goes to the client."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def is_enabled() -> bool:
    return bool(settings.keycloak_issuer and settings.backend_base_url and settings.backend_api_key)


def _unverified_claims(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def looks_like_keycloak_token(token: str) -> bool:
    """Whether ``token`` claims to come from the configured Keycloak realm."""
    if not settings.keycloak_issuer:
        return False
    claims = _unverified_claims(token.strip())
    if claims is None:
        return False
    return claims.get("iss") == settings.keycloak_issuer.rstrip("/")


def _cache_key(token: str, team_id: str | None) -> str:
    # Never keep the raw token in memory; scope entries by team.
    return hashlib.sha256(f"{token}|{team_id or ''}".encode()).hexdigest()


def _prune(now: float) -> None:
    if len(_cache) < settings.sso_cache_max_entries:
        return
    for k in [k for k, (_, exp) in _cache.items() if exp <= now]:
        del _cache[k]
    if len(_cache) >= settings.sso_cache_max_entries:
        _cache.clear()


async def resolve_user_key(token: str, team_id: str | None) -> str:
    """Return the LiteLLM key for the token's user (and team), or raise SsoAuthError.

    Successful lookups are cached for ``sso_cache_ttl`` seconds but never past
    the token's own expiry. Failures are not cached.
    """
    ck = _cache_key(token, team_id)
    now = time.monotonic()
    hit = _cache.get(ck)
    if hit is not None and hit[1] > now:
        return hit[0]

    url = f"{settings.backend_base_url.rstrip('/')}/api/external/token-auth"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=settings.ssl_verify) as client:
            resp = await client.post(
                url,
                headers={"X-Api-Key": settings.backend_api_key},
                json={"token": token, "team_id": team_id},
            )
    except httpx.HTTPError as e:
        raise SsoAuthError(503, "Authentication service unavailable") from e

    if resp.status_code == 401:
        raise SsoAuthError(401, "Invalid or expired token")
    if resp.status_code == 403:
        raise SsoAuthError(403, _detail(resp) or "Forbidden")
    if resp.status_code != 200:
        raise SsoAuthError(503, "Authentication service unavailable")

    data = resp.json()
    key = data.get("litellm_key")
    if not key:
        raise SsoAuthError(503, "Authentication service unavailable")

    ttl = float(settings.sso_cache_ttl)
    expires_at = data.get("expires_at")
    if expires_at:
        ttl = min(ttl, float(expires_at) - time.time())
    if ttl > 0:
        _prune(now)
        _cache[ck] = (key, now + ttl)
    return key


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return ""
    return detail if isinstance(detail, str) else ""
