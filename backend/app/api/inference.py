"""Reverse proxy for LiteLLM inference endpoints (/v1/*).

Transparently forwards all /v1/* requests to the LiteLLM proxy so that
SDK users only need a single endpoint (our backend) for both management
and inference.  Streaming (SSE) responses are relayed in real time.
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from app.config import settings

router = APIRouter(tags=["inference"])

_LITELLM_BASE = settings.litellm_base_url.rstrip("/")

# Inference can take a long time (long outputs, thinking models).
_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

# Hop-by-hop headers that must NOT be forwarded between proxy hops.
_HOP_HEADERS = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
    }
)


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter incoming request headers for upstream forwarding."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_HEADERS}


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Filter upstream response headers for the client."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_HEADERS and k.lower() != "content-length"}


@router.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_inference(request: Request, path: str) -> Response:
    """Transparent reverse proxy to LiteLLM for all ``/v1/*`` endpoints.

    * Forwards the ``Authorization`` header (LiteLLM virtual key) as-is.
    * Detects ``text/event-stream`` responses and relays SSE chunks in
      real time via :class:`StreamingResponse`.
    * Non-streaming responses are read in full and returned directly.
    """

    # ── Build target URL ─────────────────────────────────────────
    target_url = f"{_LITELLM_BASE}/v1/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # ── Forward headers & body ───────────────────────────────────
    fwd_headers = _forward_headers(dict(request.headers))
    body = await request.body()

    # ── Upstream request (always stream so we can inspect headers) ─
    client = httpx.AsyncClient(timeout=_TIMEOUT, verify=settings.ssl_verify)
    try:
        upstream = await client.send(
            client.build_request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                content=body,
            ),
            stream=True,
        )
    except httpx.ConnectError:
        await client.aclose()
        return Response(
            content=b'{"error":{"message":"LiteLLM proxy is unreachable","type":"proxy_error"}}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        await client.aclose()
        return Response(
            content=b'{"error":{"message":"LiteLLM proxy timed out","type":"proxy_error"}}',
            status_code=504,
            media_type="application/json",
        )

    resp_headers = _response_headers(upstream.headers)
    content_type = upstream.headers.get("content-type", "")

    # ── SSE streaming response ───────────────────────────────────
    if "text/event-stream" in content_type:

        async def _relay() -> bytes:  # type: ignore[override]
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk  # type: ignore[misc]
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            content=_relay(),
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type="text/event-stream",
        )

    # ── Regular (non-streaming) response ─────────────────────────
    content = await upstream.aread()
    await upstream.aclose()
    await client.aclose()

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type or "application/json",
    )
