"""Keyless authentication for trusted upstream systems.

Some systems call the gateway without a LiteLLM key, presenting only a system
id + shared secret (and an identity header such as ``emp-no``).  This module
resolves those credentials to the system's LiteLLM virtual key by asking the
management backend, caching successful lookups for a short TTL.
"""

import hashlib
import time

import httpx

from app.config import settings

# Cache: secret-scoped key -> (litellm_key, expires_at_monotonic).
_cache: dict[str, tuple[str, float]] = {}

_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)


def _cache_key(system_id: str, secret: str) -> str:
    # Include a hash of the secret so a rotated secret never hits a stale entry,
    # and never keep the raw secret in memory.
    digest = hashlib.sha256(f"{system_id}:{secret}".encode()).hexdigest()
    return digest


def is_enabled() -> bool:
    """Whether the keyless trusted-system path is configured."""
    return bool(settings.backend_base_url and settings.backend_api_key)


async def resolve_system_key(system_id: str, secret: str) -> str | None:
    """Return the LiteLLM key for a trusted system, or None if not authorised.

    Looks up an in-memory TTL cache first, then falls back to the backend
    ``/api/external/system-auth`` endpoint.  Failures are not cached so that
    re-enabling a system or rotating a secret takes effect immediately.
    """
    if not is_enabled():
        return None

    ck = _cache_key(system_id, secret)
    hit = _cache.get(ck)
    if hit is not None and hit[1] > time.monotonic():
        return hit[0]

    url = f"{settings.backend_base_url.rstrip('/')}/api/external/system-auth"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=settings.ssl_verify) as client:
            resp = await client.post(
                url,
                headers={"X-Api-Key": settings.backend_api_key},
                json={"system_id": system_id, "secret": secret},
            )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    key = resp.json().get("litellm_key")
    if not key:
        return None

    _cache[ck] = (key, time.monotonic() + settings.system_auth_cache_ttl)
    return key
