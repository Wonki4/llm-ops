"""Reverse proxy for LiteLLM inference endpoints (/v1/*).

Transparently forwards all /v1/* requests to the LiteLLM proxy so that
SDK users have a single inference endpoint.  Streaming (SSE) responses are
relayed in real time.
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from app.config import settings
from app.trusted_system import resolve_system_key

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

# LiteLLM virtual keys must start with this prefix.
_KEY_PREFIX = "sk-"

# Headers LiteLLM reads the virtual key from (lowercased).  Different clients
# use different ones, so we normalise the credential in every recognised header.
#   authorization      -> OpenAI / Cohere / Mistral / OpenAI-compatible SDKs
#   x-api-key          -> Anthropic SDK
#   api-key            -> Azure OpenAI SDK
#   x-goog-api-key     -> Google AI Studio (Gemini)
#   x-litellm-api-key  -> LiteLLM custom header
_CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "api-key",
        "x-goog-api-key",
        "x-litellm-api-key",
    }
)


def _ensure_sk(token: str) -> str:
    """Prepend the ``sk-`` prefix to a bare key when it is missing."""
    token = token.strip()
    if token and not token.startswith(_KEY_PREFIX):
        return f"{_KEY_PREFIX}{token}"
    return token


def _ensure_sk_credential(value: str) -> str:
    """Inject the ``sk-`` prefix into a credential header value.

    Handles both bare keys (``mykey``) and ``Bearer``-scheme values
    (``Bearer mykey``).  Non-key schemes (``Basic`` for Langfuse, AWS SigV4
    from LangChain) are left untouched so we never mangle them.
    """
    for scheme in ("Bearer ", "bearer "):
        if value.startswith(scheme):
            return f"{scheme}{_ensure_sk(value[len(scheme):])}"
    if value.startswith("Basic ") or value.startswith("AWS4-HMAC-SHA256"):
        return value
    return _ensure_sk(value)


def _forward_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter hop-by-hop headers and normalise auth credentials.

    LiteLLM only accepts virtual keys prefixed with ``sk-``.  Clients send the
    key via different headers and may omit the prefix, so inject ``sk-`` into
    every recognised credential header before forwarding upstream.
    """
    fwd = {k: v for k, v in headers.items() if k.lower() not in _HOP_HEADERS}
    for name, value in fwd.items():
        if name.lower() in _CREDENTIAL_HEADERS:
            fwd[name] = _ensure_sk_credential(value)
    return fwd


def _has_credential(headers: dict[str, str]) -> bool:
    """Whether the request already carries a recognised LiteLLM credential."""
    return any(name.lower() in _CREDENTIAL_HEADERS and value.strip() for name, value in headers.items())


def _unauthorized() -> Response:
    return Response(
        content=b'{"error":{"message":"Unauthorized system","type":"auth_error"}}',
        status_code=401,
        media_type="application/json",
    )


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
    incoming = dict(request.headers)
    fwd_headers = _forward_headers(incoming)

    # ── Keyless trusted-system auth ──────────────────────────────
    # No LiteLLM credential, but a system id + shared secret were presented:
    # resolve them to the system's virtual key (identity, e.g. emp-no, is left
    # in place for LiteLLM end-user tracking).
    if not _has_credential(incoming):
        system_id = incoming.get(settings.system_id_header.lower(), "").strip()
        secret = incoming.get(settings.system_secret_header.lower(), "").strip()
        if system_id and secret:
            key = await resolve_system_key(system_id, secret)
            if key is None:
                return _unauthorized()
            fwd_headers["authorization"] = f"Bearer {_ensure_sk(key)}"
            # Never forward the shared secret (or system id) upstream.
            fwd_headers.pop(settings.system_secret_header.lower(), None)
            fwd_headers.pop(settings.system_id_header.lower(), None)

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
