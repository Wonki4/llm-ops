"""Unit tests for the ArgoCD connection registry + REST client + llm-d wiring."""

from app.db.models.custom_argocd_connection import CustomArgocdConnection
from app.db.models.custom_llmd_stack import CustomLlmdStack


def test_llmd_stack_has_argocd_connection_fk():
    assert "argocd_connection_id" in CustomLlmdStack.__table__.columns.keys()


# ── ArgoCD REST client (httpx MockTransport) ─────────────────────────────────

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.clients.argocd import ArgoCDClient  # noqa: E402


@pytest.mark.asyncio
async def test_get_application_returns_none_on_404():
    transport = httpx.MockTransport(lambda req: httpx.Response(404, json={"error": "not found"}))
    client = ArgoCDClient("https://argo.local", "tok", transport=transport)
    assert await client.get_application("missing") is None


@pytest.mark.asyncio
async def test_create_application_posts_with_bearer_token():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["path"] = req.url.path
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"metadata": {"name": "x"}})

    client = ArgoCDClient("https://argo.local", "tok", transport=httpx.MockTransport(handler))
    await client.create_application({"metadata": {"name": "x"}})
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/applications"
    assert seen["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_version_reads_capitalised_key():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"Version": "v2.11.0"}))
    client = ArgoCDClient("https://argo.local", "tok", transport=transport)
    assert await client.version() == "v2.11.0"


# ── connections API serialization masks the token ────────────────────────────

import types  # noqa: E402
import uuid  # noqa: E402

from app.api.argocd_connections import _serialize  # noqa: E402


def test_connection_serialize_masks_token():
    conn = types.SimpleNamespace(
        id=uuid.uuid4(), name="prod-argo", server_url="https://argo.local",
        token_encrypted="gAAAA-secret-ciphertext", insecure_skip_verify=False,
        is_default=True, description=None, created_by="admin",
        created_at=None, updated_at=None,
    )
    out = _serialize(conn)
    assert "token" not in out and "token_encrypted" not in out
    assert out["has_token"] is True
    assert "gAAAA-secret-ciphertext" not in str(out)


def test_argocd_connection_columns():
    cols = set(CustomArgocdConnection.__table__.columns.keys())
    assert {
        "id", "name", "server_url", "token_encrypted", "insecure_skip_verify",
        "is_default", "description", "created_by", "updated_by",
        "created_at", "updated_at",
    } <= cols
    # The plaintext token must never be a column.
    assert "token" not in cols
