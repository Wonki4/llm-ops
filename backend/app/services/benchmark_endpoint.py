"""OpenAI-compatible target discovery without storing or forwarding portal credentials."""

import json

import httpx
from fastapi import HTTPException

from app.services.litellm_registration import _http_base, with_v1


def endpoint_url(value: str) -> str:
    try:
        return _http_base(value)
    except ValueError:
        raise HTTPException(400, "API Base URL must be HTTP(S), without credentials, query or fragment") from None


async def endpoint_models(base_url: str, api_key: str) -> dict:
    base = endpoint_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        # Redirects must never send the user's API key to a different endpoint.
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            async with client.stream("GET", with_v1(base) + "/models", headers=headers) as response:
                if response.status_code != 200:
                    raise HTTPException(502, f"Model discovery returned HTTP {response.status_code}; check the URL/key or enter the model name")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1_048_576:
                        raise HTTPException(502, "Model discovery response is too large; enter the model name")
                data = json.loads(content)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError()
        models = sorted({r["id"] for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"].strip()})
        if not models:
            raise ValueError()
    except (httpx.HTTPError, ValueError):
        # Upstream bodies/exception messages may contain the supplied credential.
        raise HTTPException(502, "Could not discover models; check the URL/key or enter the model name") from None
    return {"base_url": base, "models": models}
