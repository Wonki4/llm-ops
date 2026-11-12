"""Prepare OIDC kubeconfigs for the portal's long-running Kubernetes clients.

The Kubernetes asyncio loader can read an ``auth-provider: oidc`` ID token, but
its refresh path requires a client secret and does not persist rotated refresh
tokens when loading an in-memory dict. Refresh here, persist the resulting
kubeconfig at the call site, then give the client a one-request bearer token.
"""

import base64
import copy
import json
import ssl
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


class OidcKubeconfigError(ValueError):
    """An OIDC kubeconfig cannot provide a usable Kubernetes API token."""


_REFRESH_SKEW_SECONDS = 30


def _selected_user(kubeconfig: dict, context: str) -> dict | None:
    context_entry = next((c for c in kubeconfig.get("contexts", []) if c.get("name") == context), None)
    user_name = (context_entry or {}).get("context", {}).get("user")
    if not user_name:
        return None
    user_entry = next((u for u in kubeconfig.get("users", []) if u.get("name") == user_name), None)
    return user_entry.get("user") if user_entry else None


def oidc_provider_config(kubeconfig: dict, context: str) -> dict | None:
    """Return the selected user's OIDC config, validating fields needed for refresh."""
    user = _selected_user(kubeconfig, context)
    provider = (user or {}).get("auth-provider")
    if not isinstance(provider, dict) or provider.get("name") != "oidc":
        return None
    fields = provider.get("config")
    if not isinstance(fields, dict):
        raise OidcKubeconfigError("OIDC kubeconfig is missing auth-provider.config")
    for key in ("client-id", "idp-issuer-url"):
        if not fields.get(key):
            raise OidcKubeconfigError(f"OIDC kubeconfig is missing {key}")
    if not fields.get("id-token") and not fields.get("refresh-token"):
        raise OidcKubeconfigError("OIDC kubeconfig needs an id-token or refresh-token")
    issuer = urlparse(fields["idp-issuer-url"])
    if issuer.scheme not in ("http", "https") or not issuer.netloc:
        raise OidcKubeconfigError("OIDC idp-issuer-url must be an absolute HTTP(S) URL")
    return fields


def _token_expiring(token: str | None) -> bool:
    if not token:
        return True
    try:
        payload = token.split(".")[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(decoded["exp"]) <= time.time() + _REFRESH_SKEW_SECONDS
    except (IndexError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise OidcKubeconfigError("OIDC id-token is not a valid JWT with an exp claim") from e


def _tls_context(fields: dict) -> ssl.SSLContext:
    try:
        if fields.get("idp-certificate-authority-data"):
            pem = base64.b64decode(fields["idp-certificate-authority-data"], validate=True).decode("utf-8")
            return ssl.create_default_context(cadata=pem)
        if fields.get("idp-certificate-authority"):
            path = Path(fields["idp-certificate-authority"])
            if not path.is_file():
                raise OidcKubeconfigError(
                    "OIDC idp-certificate-authority is unavailable in the backend; "
                    "embed it as idp-certificate-authority-data"
                )
            return ssl.create_default_context(cafile=str(path))
        return ssl.create_default_context()
    except (ValueError, OSError, UnicodeError) as e:
        if isinstance(e, OidcKubeconfigError):
            raise
        raise OidcKubeconfigError("OIDC identity provider CA certificate is invalid") from e


async def _refresh_token(fields: dict) -> None:
    if not fields.get("refresh-token"):
        raise OidcKubeconfigError("OIDC id-token is expiring and the kubeconfig has no refresh-token")
    issuer = fields["idp-issuer-url"].rstrip("/")
    async with httpx.AsyncClient(verify=_tls_context(fields), timeout=10.0) as http:
        try:
            discovery = await http.get(f"{issuer}/.well-known/openid-configuration")
            discovery.raise_for_status()
            metadata = discovery.json()
            if metadata.get("issuer", "").rstrip("/") != issuer:
                raise OidcKubeconfigError("OIDC discovery issuer does not match idp-issuer-url")
            token_endpoint = metadata.get("token_endpoint", "")
            endpoint = urlparse(token_endpoint)
            if endpoint.scheme not in ("http", "https") or not endpoint.netloc:
                raise OidcKubeconfigError("OIDC discovery has no valid token_endpoint")
            if issuer.startswith("https://") and endpoint.scheme != "https":
                raise OidcKubeconfigError("OIDC token_endpoint must use HTTPS when the issuer uses HTTPS")
            data = {
                "grant_type": "refresh_token",
                "client_id": fields["client-id"],
                "refresh_token": fields["refresh-token"],
            }
            if fields.get("client-secret"):
                data["client_secret"] = fields["client-secret"]
            response = await http.post(token_endpoint, data=data)
            response.raise_for_status()
            refreshed = response.json()
        except httpx.HTTPStatusError as e:
            raise OidcKubeconfigError(f"OIDC token refresh failed (HTTP {e.response.status_code})") from e
        except (httpx.RequestError, ValueError) as e:
            if isinstance(e, OidcKubeconfigError):
                raise
            raise OidcKubeconfigError("OIDC token refresh failed") from e
    if not refreshed.get("id_token"):
        raise OidcKubeconfigError("OIDC token refresh returned no id_token")
    fields["id-token"] = refreshed["id_token"]
    fields["refresh-token"] = refreshed.get("refresh_token") or fields["refresh-token"]


async def prepare_oidc_kubeconfig(
    kubeconfig: dict, context: str, *, allow_refresh: bool
) -> tuple[dict, bool]:
    """Return a client-ready config; mutate the original only when tokens rotate.

    The returned copy has a plain bearer token in the selected user so the
    Kubernetes SDK cannot perform a second, unpersisted OIDC refresh.
    """
    fields = oidc_provider_config(kubeconfig, context)
    if fields is None:
        return kubeconfig, False
    refreshed = _token_expiring(fields.get("id-token"))
    if refreshed:
        if not allow_refresh:
            raise OidcKubeconfigError("OIDC id-token is expiring; save the cluster, then test the saved connection")
        await _refresh_token(fields)
        if _token_expiring(fields["id-token"]):
            raise OidcKubeconfigError("OIDC refresh returned an already expiring id_token")
    client_config = copy.deepcopy(kubeconfig)
    selected = _selected_user(client_config, context)
    assert selected is not None
    selected.clear()
    selected["token"] = fields["id-token"]
    return client_config, refreshed
