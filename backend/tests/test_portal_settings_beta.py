"""The personal-keys beta switch round-trips through the portal settings API."""

from unittest.mock import AsyncMock, MagicMock


def _rows(pairs):
    r = MagicMock()
    r.mappings.return_value = [{"key": k, "value": v} for k, v in pairs]
    r.scalar_one_or_none.return_value = next((v for k, v in pairs if k == "personal_keys_beta_enabled"), None)
    return r


async def test_settings_expose_the_beta_switch_default_off(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_rows([("default_tpm_limit", "100000")]))
    async with client_for_user(super_user) as client:
        resp = await client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["personal_keys_beta_enabled"] is False


async def test_settings_store_the_beta_switch_as_text(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_rows([("personal_keys_beta_enabled", "true")]))
    mock_db.commit = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.put("/api/settings", json={"personal_keys_beta_enabled": True})
    assert resp.status_code == 200
    assert resp.json()["personal_keys_beta_enabled"] is True
    writes = [
        c for c in mock_db.execute.await_args_list if c.args and "INSERT INTO custom_portal_settings" in str(c.args[0])
    ]
    assert writes and writes[0].args[1] == {
        "key": "personal_keys_beta_enabled",
        "value": "true",
        "updated_by": "admin001",
    }


async def test_personal_keys_beta_enabled_helper(mock_db):
    from app.api.portal_settings import personal_keys_beta_enabled

    mock_db.execute = AsyncMock(return_value=_rows([("personal_keys_beta_enabled", "false")]))
    assert await personal_keys_beta_enabled(mock_db) is False
    mock_db.execute = AsyncMock(return_value=_rows([("personal_keys_beta_enabled", "true")]))
    assert await personal_keys_beta_enabled(mock_db) is True
