"""OIDC cluster access: public-client refresh, rotation, and CA upload."""

import base64
import json
import time
import types
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs

import httpx
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.api.k8s_clusters import _parse_kubeconfig
from app.clients.k8s import K8sClient
from app.services import crypto, oidc_kubeconfig
from app.services.clusters import _registered_oidc_kubeconfig

ISSUER = "https://keycloak.example.test/realms/cluster"
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


def _kubeconfig(id_token: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "target", "cluster": {"server": "https://k8s.example.test"}}],
        "contexts": [{"name": "ctx", "context": {"cluster": "target", "user": "oidc-user"}}],
        "users": [{"name": "oidc-user", "user": {"auth-provider": {"name": "oidc", "config": {
            "client-id": "portal-public",
            "id-token": id_token,
            "idp-issuer-url": ISSUER,
            "refresh-token": "old-refresh",
        }}}}],
    }


async def test_registered_public_client_refresh_persists_rotated_tokens(monkeypatch):
    """A saved public-client config refreshes without a secret and stores rotation."""
    new_token = _jwt(int(time.time()) + 240)
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT})
        assert str(request.url) == TOKEN_ENDPOINT
        body = parse_qs(request.content.decode())
        assert body == {
            "grant_type": ["refresh_token"],
            "client_id": ["portal-public"],
            "refresh_token": ["old-refresh"],
        }
        return httpx.Response(200, json={"id_token": new_token, "refresh_token": "new-refresh"})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(
        oidc_kubeconfig.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )

    cluster_id = uuid.uuid4()
    row = types.SimpleNamespace(
        id=cluster_id,
        context="ctx",
        kubeconfig_encrypted=crypto.encrypt(yaml.safe_dump(_kubeconfig(_jwt(0)))),
    )
    result = types.SimpleNamespace(scalar_one_or_none=lambda: row)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    with patch("app.services.clusters.async_session_factory", return_value=session):
        client_config, context = await _registered_oidc_kubeconfig(cluster_id)

    assert context == "ctx"
    assert client_config["users"][0]["user"] == {"token": new_token}
    stored = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    stored_fields = stored["users"][0]["user"]["auth-provider"]["config"]
    assert stored_fields["id-token"] == new_token
    assert stored_fields["refresh-token"] == "new-refresh"
    assert len(requests) == 2
    session.commit.assert_awaited_once()


async def test_unsaved_expired_oidc_token_requires_saved_cluster():
    with pytest.raises(oidc_kubeconfig.OidcKubeconfigError, match="save the cluster"):
        await oidc_kubeconfig.prepare_oidc_kubeconfig(_kubeconfig(_jwt(0)), "ctx", allow_refresh=False)


async def test_five_minute_oidc_token_is_usable_without_refresh():
    token = _jwt(int(time.time()) + 240)
    ready, refreshed = await oidc_kubeconfig.prepare_oidc_kubeconfig(
        _kubeconfig(token), "ctx", allow_refresh=False
    )
    assert refreshed is False
    assert ready["users"][0]["user"] == {"token": token}


async def test_valid_oidc_token_reaches_kubernetes_client_as_bearer():
    token = _jwt(int(time.time()) + 3600)
    api_client = await K8sClient(kubeconfig=_kubeconfig(token), context="ctx")._api_client()
    try:
        assert api_client.configuration.api_key["BearerToken"] == f"Bearer {token}"
    finally:
        await api_client.close()


def test_oidc_ca_pem_upload_embeds_certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test CA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    config = _kubeconfig(_jwt(int(time.time()) + 3600))
    config["users"][0]["user"]["auth-provider"]["config"]["idp-certificate-authority"] = "/local/ca.pem"

    parsed, _ = _parse_kubeconfig(yaml.safe_dump(config), "ctx", pem)
    fields = parsed["users"][0]["user"]["auth-provider"]["config"]
    assert base64.b64decode(fields["idp-certificate-authority-data"]).decode() == pem
    assert "idp-certificate-authority" not in fields

    with pytest.raises(HTTPException, match="Invalid OIDC CA certificate PEM"):
        _parse_kubeconfig(yaml.safe_dump(config), "ctx", "not a PEM certificate")
