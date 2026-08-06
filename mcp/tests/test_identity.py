import json

import pytest
from jose import jwt

from app.identity import identity_from_key

SECRET = "litellm-portal-key-sign"


def _key(user_id="E12345", team_id="team-1"):
    """Mint a key exactly like backend keys.py:_generate_sk_jwt."""
    sub = json.dumps(
        {"keyId": 10001, "prjId": team_id, "keyType": "PRJ", "regUserId": user_id, "iat": 1},
        separators=(",", ":"),
    )
    return "sk-" + jwt.encode({"sub": sub}, SECRET, algorithm="HS256")


def test_decodes_reguserid_and_team():
    ident = identity_from_key(_key(), SECRET)
    assert ident.user_id == "E12345"
    assert ident.key_team_id == "team-1"


def test_decodes_bare_jwt_without_sk_prefix():
    # The portal returns keys with the sk- prefix STRIPPED (keys.py removeprefix),
    # so the token a caller actually holds has no sk-. It must still decode.
    bare = _key().removeprefix("sk-")
    assert not bare.startswith("sk-")
    ident = identity_from_key(bare, SECRET)
    assert ident.user_id == "E12345"
    assert ident.key_team_id == "team-1"


def test_rejects_tampered_signature():
    with pytest.raises(ValueError):
        identity_from_key(_key()[:-2] + "xx", SECRET)


def test_rejects_wrong_secret():
    with pytest.raises(ValueError):
        identity_from_key(_key(), "wrong-secret")


def test_rejects_garbage_token():
    # A non-JWT token is rejected on decode (prefix no longer matters).
    with pytest.raises(ValueError):
        identity_from_key("not-a-key", SECRET)


def test_rejects_empty_token():
    with pytest.raises(ValueError):
        identity_from_key("", SECRET)
    with pytest.raises(ValueError):
        identity_from_key("   ", SECRET)
