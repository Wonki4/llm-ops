"""Resolve the caller's identity from their ``sk-<jwt>`` LiteLLM key.

The key is minted by the backend (keys.py:_generate_sk_jwt) as ``sk-`` + a
HS256 JWT whose ``sub`` claim is a JSON string:
``{"keyId", "prjId"(=team_id), "keyType", "regUserId"(=user_id), "iat"}``.
We hold the same signing secret, so we decode it locally — no LiteLLM call.
"""

import json

from jose import JWTError, jwt
from pydantic import BaseModel


class Identity(BaseModel):
    user_id: str  # regUserId (사번)
    key_team_id: str  # prjId — the team the key was minted for


def identity_from_key(token: str, secret: str) -> Identity:
    """Decode a ``sk-<jwt>`` key into an Identity. Raises ValueError on any
    failure (missing prefix, bad signature, malformed payload)."""
    if not isinstance(token, str) or not token.startswith("sk-"):
        raise ValueError("not a portal key (missing 'sk-' prefix)")
    raw = token[len("sk-"):]
    try:
        # The key JWT has no exp/aud claims; disable audience verification.
        payload = jwt.decode(raw, secret, algorithms=["HS256"], options={"verify_aud": False})
        inner = json.loads(payload["sub"])
        return Identity(user_id=str(inner["regUserId"]), key_team_id=str(inner["prjId"]))
    except (JWTError, KeyError, ValueError, TypeError) as e:
        raise ValueError(f"invalid portal key: {e}") from e
