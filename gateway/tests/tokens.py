"""Unsigned JWT look-alikes for gateway tests (the gateway only peeks at claims)."""

import base64
import json

ISSUER = "https://sso.example.com/realms/litellm"


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def make_jwt(payload: dict, alg: str = "RS256") -> str:
    return f"{_b64({'alg': alg, 'typ': 'JWT'})}.{_b64(payload)}.c2ln"


def keycloak_token(**claims) -> str:
    return make_jwt({"iss": ISSUER, "sub": "kc-sub", "preferred_username": "user001", **claims})


def portal_key() -> str:
    """A portal-issued LiteLLM key as users see it: HS256 JWT, no iss, no sk- prefix."""
    return make_jwt({"sub": json.dumps({"keyId": 10001, "prjId": "team-1", "regUserId": "USER001"})}, alg="HS256")
