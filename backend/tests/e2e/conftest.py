"""E2E test fixtures - runs against live Docker environment."""

import os

import httpx
import pytest


BACKEND_URL = os.getenv("E2E_BACKEND_URL", "http://localhost:8002")
KEYCLOAK_URL = os.getenv("E2E_KEYCLOAK_URL", "http://localhost:8082")
KEYCLOAK_REALM = os.getenv("E2E_KEYCLOAK_REALM", "litellm")
KEYCLOAK_CLIENT_ID = os.getenv("E2E_KEYCLOAK_CLIENT_ID", "litellm-portal")
KEYCLOAK_CLIENT_SECRET = os.getenv("E2E_KEYCLOAK_CLIENT_SECRET", "change-me-in-keycloak")
# Admin user credentials (must exist in Keycloak with super_user role/group)
ADMIN_USERNAME = os.getenv("E2E_ADMIN_USERNAME", "admin001")
ADMIN_PASSWORD = os.getenv("E2E_ADMIN_PASSWORD", "admin001")


@pytest.fixture(scope="session")
def backend_url() -> str:
    return BACKEND_URL


@pytest.fixture(scope="session")
def admin_session(backend_url: str) -> httpx.Client:
    """Get an authenticated session via Keycloak direct access grant, then exchange for portal session."""
    # 1. Get access token from Keycloak
    token_url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    token_resp = httpx.post(
        token_url,
        data={
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT_ID,
            "client_secret": KEYCLOAK_CLIENT_SECRET,
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
        },
    )
    if token_resp.status_code != 200:
        pytest.skip(f"Cannot authenticate with Keycloak: {token_resp.status_code} {token_resp.text}")

    access_token = token_resp.json()["access_token"]

    # 2. Use Bearer token directly against backend API
    client = httpx.Client(
        base_url=backend_url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )

    # 3. Verify auth works
    me_resp = client.get("/api/me")
    if me_resp.status_code != 200:
        pytest.skip(f"Cannot authenticate with backend: {me_resp.status_code}")

    return client
