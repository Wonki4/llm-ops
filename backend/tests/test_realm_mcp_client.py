"""The sample Keycloak client the local realms ship for the portal MCP."""

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REALMS = [
    REPO / "deploy/keycloak/litellm-realm.json",
    REPO / "deploy/helm/litellm-platform/files/litellm-realm.json",
]


@pytest.mark.parametrize("path", REALMS, ids=lambda p: str(p.relative_to(REPO)))
def test_sample_mcp_client(path):
    c = next(c for c in json.loads(path.read_text())["clients"] if c["clientId"] == "portal-mcp")

    assert c["publicClient"] is True and c["standardFlowEnabled"] is True
    assert c["directAccessGrantsEnabled"] is False and c.get("implicitFlowEnabled") is False
    assert c.get("serviceAccountsEnabled") is False
    assert c["attributes"]["pkce.code.challenge.method"] == "S256"
    assert c["redirectUris"] == ["http://localhost:8765/callback"]
    assert c["optionalClientScopes"] == []  # no offline_access: no offline tokens
    assert {"basic", "profile", "roles"} <= set(c["defaultClientScopes"])
    assert c["fullScopeAllowed"] is True

    aud = [m for m in c["protocolMappers"] if m["protocolMapper"] == "oidc-audience-mapper"]
    assert len(aud) == 1  # only the MCP audience; the portal audience is not added here
    assert aud[0]["config"]["included.custom.audience"] == "portal-mcp"
    assert aud[0]["config"]["access.token.claim"] == "true"
    assert any(m["name"] == "preferred_username_mapper" for m in c["protocolMappers"])
