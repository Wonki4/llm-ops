import hashlib
import secrets
import time
from base64 import urlsafe_b64encode
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from jose import jwt as jose_jwt

from app.auth.session import (
    SessionData,
    clear_session_cookie,
    decode_temp,
    encode_session,
    encode_temp,
    load_session,
    refresh_session_if_needed,
    set_session_cookie,
)
from app.config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

ALLOWED_RETURN_PREFIXES = ("/",)


def _safe_return_to(return_to: str) -> str:
    if return_to and any(return_to.startswith(p) for p in ALLOWED_RETURN_PREFIXES):
        if not return_to.startswith("//"):
            return return_to
    return "/teams"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@router.get("/login")
async def login(return_to: str = "/teams") -> Response:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)

    temp_data = {
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "return_to": _safe_return_to(return_to),
    }
    temp_cookie = encode_temp(temp_data)

    params: dict[str, str] = {
        "client_id": settings.keycloak_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": settings.keycloak_redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    if settings.keycloak_idp_hint:
        params["kc_idp_hint"] = settings.keycloak_idp_hint

    authorize_url = f"{settings.keycloak_issuer}/protocol/openid-connect/auth?{urlencode(params)}"

    response = RedirectResponse(url=authorize_url, status_code=302)
    response.set_cookie(
        "_oauth_temp",
        temp_cookie,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=300,
        path="/",
    )
    return response


@router.get("/callback")
async def callback(request: Request, code: str, state: str) -> Response:
    temp_cookie = request.cookies.get("_oauth_temp")
    if not temp_cookie:
        raise HTTPException(status_code=400, detail="Missing auth state cookie")

    temp_data = decode_temp(temp_cookie)
    if not temp_data:
        raise HTTPException(status_code=400, detail="Invalid auth state")

    stored_state = temp_data["state"]
    stored_nonce = temp_data["nonce"]
    stored_verifier = temp_data["code_verifier"]
    stored_return_to = temp_data.get("return_to", "/teams")

    if stored_state != state:
        raise HTTPException(status_code=400, detail="State mismatch")

    async with httpx.AsyncClient(verify=settings.ssl_verify) as client:
        resp = await client.post(
            f"{settings.keycloak_internal_issuer}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.keycloak_client_id,
                "client_secret": settings.keycloak_client_secret,
                "redirect_uri": settings.keycloak_redirect_uri,
                "code": code,
                "code_verifier": stored_verifier,
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Token exchange failed")
        token_data = resp.json()

    id_token_raw = token_data.get("id_token", "")
    if id_token_raw:
        id_claims = jose_jwt.get_unverified_claims(id_token_raw)
        if id_claims.get("nonce") != stored_nonce:
            raise HTTPException(status_code=400, detail="Nonce mismatch")

    access_claims = jose_jwt.get_unverified_claims(token_data["access_token"])

    user_id = access_claims.get("preferred_username") or access_claims.get("sub", "")
    realm_roles = access_claims.get("realm_access", {}).get("roles", [])
    client_roles = access_claims.get("resource_access", {}).get(settings.jwt_audience, {}).get("roles", [])
    groups = access_claims.get("groups", [])

    session = SessionData(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        id_token=id_token_raw,
        expires_at=int(time.time()) + token_data.get("expires_in", 1800),
        user_id=user_id,
        email=access_claims.get("email", ""),
        name=access_claims.get("name", ""),
        roles=realm_roles + client_roles,
        groups=groups,
    )

    session_value = encode_session(session)
    return {
        "session_value": session_value,
        "redirect_to": stored_return_to,
        "cookie_name": settings.session_cookie_name,
        "max_age": settings.session_max_age,
    }


@router.get("/me")
async def me(request: Request) -> dict:
    response = Response()
    session = await refresh_session_if_needed(request, response)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {
        "user_id": session.user_id,
        "email": session.email,
        "name": session.name,
        "roles": session.roles,
    }


@router.get("/logout")
async def logout(request: Request) -> Response:
    session = load_session(request)
    id_token_hint = session.id_token if session else ""

    params: dict[str, str] = {
        "post_logout_redirect_uri": f"{settings.frontend_url}/login",
        "client_id": settings.keycloak_client_id,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint

    logout_url = f"{settings.keycloak_issuer}/protocol/openid-connect/logout?{urlencode(params)}"

    response = RedirectResponse(url=logout_url, status_code=302)
    clear_session_cookie(response)
    return response
