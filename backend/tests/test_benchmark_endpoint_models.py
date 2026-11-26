import httpx
import pytest

from app.services import benchmark_endpoint


@pytest.fixture
def upstream(monkeypatch):
    calls = []
    reply = {"status": 200, "body": {"data": [{"id": "second"}, {"id": "first"}, {"id": "first"}]}}
    original = httpx.AsyncClient

    def handle(request):
        calls.append(request)
        return httpx.Response(reply["status"], json=reply["body"], headers=reply.get("headers"))

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(benchmark_endpoint.httpx, "AsyncClient", client)
    return calls, reply


@pytest.mark.parametrize("base", ["http://internal:8000", "https://proxy/custom/v1/"])
async def test_discovers_models_with_only_explicit_key(admin_client, upstream, base):
    calls, _ = upstream
    response = await admin_client.post("/api/benchmarks/endpoint-models", json={"base_url": base, "api_key": "test-private-key"})
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["first", "second"]
    assert calls[0].headers["Authorization"] == "Bearer test-private-key"
    assert calls[0].url.path == ("/v1/models" if base.startswith("http:") else "/custom/v1/models")
    assert "test-private-key" not in response.text


async def test_key_is_optional_and_portal_credentials_not_used(admin_client, upstream):
    calls, _ = upstream
    response = await admin_client.post("/api/benchmarks/endpoint-models", json={"base_url": "http://internal/v1"})
    assert response.status_code == 200
    assert "authorization" not in calls[0].headers


@pytest.mark.parametrize("status", [301, 401, 403, 404, 500])
async def test_upstream_failure_is_sanitized_and_never_redirected(admin_client, upstream, status):
    calls, reply = upstream
    reply.update(status=status, body={"error": "test-private-key"}, headers={"Location": "https://different.invalid"})
    response = await admin_client.post("/api/benchmarks/endpoint-models", json={"base_url": "https://api/v1", "api_key": "test-private-key"})
    assert response.status_code == 502
    assert "test-private-key" not in response.text
    assert len(calls) == 1


@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": [{"id": None}]}, {"data": "test-private-key"}])
async def test_invalid_catalog_can_fall_back_to_manual_model(admin_client, upstream, body):
    _, reply = upstream
    reply["body"] = body
    response = await admin_client.post("/api/benchmarks/endpoint-models", json={"base_url": "http://api"})
    assert response.status_code == 502
    assert "enter the model name" in response.text
    assert "test-private-key" not in response.text


@pytest.mark.parametrize("base", ["ftp://api", "https://name:secret@api", "http://api?key=secret", "http://api/#secret"])
async def test_rejects_unsafe_or_invalid_address(admin_client, upstream, base):
    calls, _ = upstream
    response = await admin_client.post("/api/benchmarks/endpoint-models", json={"base_url": base})
    assert response.status_code == 400 and "secret" not in response.text
    assert not calls


async def test_model_discovery_is_admin_only(user_client, upstream):
    response = await user_client.post("/api/benchmarks/endpoint-models", json={"base_url": "http://api"})
    assert response.status_code == 403
    assert not upstream[0]
