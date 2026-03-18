import json
import time
import zlib
from base64 import urlsafe_b64encode
import time
from base64 import urlsafe_b64encode
from dataclasses import asdict, dataclass, field
from hashlib import sha256

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request, Response

from app.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = urlsafe_b64encode(sha256(settings.session_secret_key.encode()).digest())
        _fernet = Fernet(key)
    return _fernet


@dataclass
class SessionData:
    access_token: str
    refresh_token: str
    expires_at: int
    user_id: str = ""
    email: str = ""
    name: str = ""
    id_token: str = ""
    roles: list[str] = field(default_factory=list)


def encode_session(data: SessionData) -> str:
    raw = json.dumps(asdict(data), separators=(',', ':')).encode()
    compressed = zlib.compress(raw, level=9)
    return _get_fernet().encrypt(compressed).decode()


def decode_session(cookie_value: str) -> SessionData | None:
    try:
        compressed = _get_fernet().decrypt(cookie_value.encode())
        raw = zlib.decompress(compressed)
        payload = json.loads(raw)
        return SessionData(**payload)
    except (InvalidToken, json.JSONDecodeError, TypeError, zlib.error):
        return None


def encode_temp(data: dict) -> str:
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def decode_temp(cookie_value: str) -> dict | None:
    try:
        return json.loads(_get_fernet().decrypt(cookie_value.encode()))
    except (InvalidToken, json.JSONDecodeError):
        return None


def set_session_cookie(response: Response, data: SessionData) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=encode_session(data),
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        domain=settings.session_cookie_domain or None,
        path="/",
    )


def load_session(request: Request) -> SessionData | None:
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return None
    return decode_session(cookie_value)


async def refresh_session_if_needed(
    request: Request,
    response: Response,
) -> SessionData | None:
    session = load_session(request)
    if session is None:
        return None

    buffer = 60
    if session.expires_at > int(time.time()) + buffer:
        return session

    if not session.refresh_token:
        clear_session_cookie(response)
        return None

    try:
        async with httpx.AsyncClient(verify=settings.ssl_verify) as client:
            resp = await client.post(
                f"{settings.keycloak_internal_issuer}/protocol/openid-connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.keycloak_client_id,
                    "client_secret": settings.keycloak_client_secret,
                    "refresh_token": session.refresh_token,
                },
            )
            if resp.status_code != 200:
                clear_session_cookie(response)
                return None

            token_data = resp.json()
            session.access_token = token_data["access_token"]
            session.refresh_token = token_data.get("refresh_token", session.refresh_token)
            session.expires_at = int(time.time()) + token_data.get("expires_in", 1800)
            if "id_token" in token_data:
                session.id_token = token_data["id_token"]

            set_session_cookie(response, session)
            return session
    except httpx.HTTPError:
        clear_session_cookie(response)
        return None
