"""Share public catalog fields and private routing fields from LiteLLM model registrations."""

import asyncio
import hashlib
import json
import time
from functools import lru_cache

import httpx

from app.clients.litellm import LiteLLMClient
from app.config import settings
from app.services import crypto
from app.services.latency_cache import LatencyCache
from app.services.model_registry import public_model_info
from app.services.serving_topology import _cloud_provider, _endpoint

_MAX_INVENTORY_BYTES = 4 * 1024 * 1024
_cache = LatencyCache(max_result_bytes=_MAX_INVENTORY_BYTES)
_SOURCE_TIMEOUT = 12


def inventory_source(litellm: LiteLLMClient) -> str:
    # A replica with a rotated encryption key must not read another key's payload.
    key = settings.encryption_key or settings.session_secret_key
    return hashlib.sha256(f"{litellm.inventory_namespace}:{key}".encode()).hexdigest()


def _inventory_fields(row):
    params = row.get("litellm_params") or {}
    url = _endpoint(params.get("api_base"))
    fields = public_model_info(row)
    fields["litellm_params"]["api_base"] = url.geturl() if url else None
    fields["is_cloud"] = bool(_cloud_provider(params, {}, url))
    return fields


def _decode(ciphertext: str) -> list[dict]:
    return json.loads(crypto.decrypt(ciphertext))


# First-open summary and list requests also share decoding. Retain only adjacent
# ticks and only inventories eligible for caching under the same byte ceiling.
_decode_cached = lru_cache(maxsize=2)(_decode)


async def read_latency_inventory(db, litellm: LiteLLMClient, *, now: float | None = None) -> list[dict]:
    """Internal read-only rows. Apply public_model_info when returning a row to the browser."""
    async def load():
        try:
            async with asyncio.timeout(_SOURCE_TIMEOUT):
                rows = await litellm.get_model_info()
            fields = [_inventory_fields(row) for row in rows if row.get("model_name")]
        except (httpx.HTTPError, TimeoutError, TypeError, ValueError, AttributeError):
            # All models on this proxy see the same failure until the next refresh.
            return {"unavailable": True}
        # Even URL paths can be signed or contain private identifiers.
        return {"inventory": crypto.encrypt(json.dumps(fields, separators=(",", ":")))}

    result = await _cache.read(
        db, scope={"model_inventory_v2": inventory_source(litellm)}, history=False,
        now=time.time() if now is None else now, load=load,
    )
    if result.get("unavailable"):
        raise httpx.RequestError("Model routing information unavailable")
    ciphertext = result["inventory"]
    return _decode_cached(ciphertext) if len(ciphertext) <= _MAX_INVENTORY_BYTES else _decode(ciphertext)
