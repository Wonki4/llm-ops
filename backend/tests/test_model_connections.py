import copy
import uuid
from unittest.mock import AsyncMock

import pytest

from app.api import model_connections as api
from app.api.models_catalog import _sanitize_litellm_info


@pytest.fixture
def registry(monkeypatch, mock_litellm):
    rows = {}

    async def get_info():
        return copy.deepcopy(list(rows.values()))

    async def create(**kwargs):
        params = {"model": kwargs["litellm_model"], **kwargs.get("extra_params", {})}
        for field in ["api_base", "api_key"]:
            if kwargs.get(field):
                params[field] = kwargs[field]
        rows[kwargs["model_id"]] = {
            "model_name": kwargs["model_name"],
            "litellm_params": params,
            "model_info": {**kwargs["model_info"], "db_model": True},
        }
        return rows[kwargs["model_id"]]

    mock_litellm.get_model_info = AsyncMock(side_effect=get_info)
    mock_litellm.create_model = AsyncMock(side_effect=create)
    mock_litellm.update_model_connection = AsyncMock(return_value={})
    mock_litellm.delete_model = AsyncMock(return_value={})
    monkeypatch.setattr(api, "managed_connections", AsyncMock(return_value={}))
    return rows


def body(**extra):
    return {
        "request_key": str(uuid.uuid4()),
        "model_name": "shared",
        "model": "openai/upstream",
        "api_key": "private-provider-token",
        **extra,
    }


async def test_retry_reuses_id_without_secret_response(admin_client, registry, mock_litellm):
    request = body()
    first = await admin_client.post("/api/admin/model-connections", json=request)
    again = await admin_client.post("/api/admin/model-connections", json=request)
    assert first.status_code == again.status_code == 201
    assert first.json()["connection"]["id"] == again.json()["connection"]["id"]
    assert again.json()["reused"]
    assert "private-provider-token" not in first.text
    mock_litellm.create_model.assert_awaited_once()
    conflict = await admin_client.post("/api/admin/model-connections", json={**request, "model": "openai/different"})
    assert conflict.status_code == 409


async def test_timeout_after_upstream_commit_is_recovered(admin_client, registry, mock_litellm):
    create = mock_litellm.create_model.side_effect

    async def timeout(**kwargs):
        await create(**kwargs)
        raise TimeoutError("private-provider-token")

    mock_litellm.create_model.side_effect = timeout
    response = await admin_client.post("/api/admin/model-connections", json=body())
    assert response.status_code == 201 and response.json()["reused"]
    assert "private-provider-token" not in response.text


async def test_patch_omits_credentials_and_other_connections(admin_client, registry, mock_litellm):
    first = (await admin_client.post("/api/admin/model-connections", json=body())).json()["connection"]
    await admin_client.post("/api/admin/model-connections", json=body())
    response = await admin_client.patch(
        f"/api/admin/model-connections/{first['id']}", json={"api_base": "https://new.example/v1", "api_key": ""}
    )
    assert response.status_code == 200
    mock_litellm.update_model_connection.assert_awaited_once_with(
        first["id"], params={"api_base": "https://new.example/v1"}, model_name=None
    )
    assert len(registry) == 2


async def test_config_and_owned_connections_are_not_edited(admin_client, registry, mock_litellm, monkeypatch):
    model_id = (await admin_client.post("/api/admin/model-connections", json=body())).json()["connection"]["id"]
    registry[model_id]["model_info"]["db_model"] = False
    assert (await admin_client.delete(f"/api/admin/model-connections/{model_id}")).status_code == 409
    registry[model_id]["model_info"]["db_model"] = True
    monkeypatch.setattr(api, "managed_connections", AsyncMock(return_value={model_id: {"id": "owned"}}))
    assert (
        await admin_client.patch(f"/api/admin/model-connections/{model_id}", json={"model": "openai/new"})
    ).status_code == 409
    mock_litellm.delete_model.assert_not_awaited()
    mock_litellm.update_model_connection.assert_not_awaited()


@pytest.mark.parametrize(
    "method,path,payload",
    [("get", "", None), ("post", "", body()), ("patch", "/id", {"model": "openai/new"}), ("delete", "/id", None)],
)
async def test_only_portal_admin_can_manage_connections(user_client, method, path, payload):
    response = await user_client.request(method, "/api/admin/model-connections" + path, json=payload)
    assert response.status_code == 403


async def test_prices_and_embedded_url_credentials_rejected(admin_client, registry):
    for extra in [
        {"extra_params": {"input_cost_per_token": 0}},
        {"api_base": "https://user:secret@host/v1"},
        {"api_base": "https://host/v1?token=secret"},
    ]:
        response = await admin_client.post("/api/admin/model-connections", json=body(**extra))
        assert response.status_code == 400 and "secret" not in response.text
    assert not registry


def test_public_model_list_does_not_leak_cloud_credentials():
    row = {
        "model_name": "shared",
        "litellm_params": {
            "model": "bedrock/model",
            "aws_secret_access_key": "secret",
            "extra_headers": {"Authorization": "secret"},
        },
        "model_info": {
            "id": "1",
            "metadata": {"secret": "secret"},
            "portal_request_hash": "secret",
            "supports_vision": True,
        },
    }
    public = _sanitize_litellm_info(row)
    assert "secret" not in str(public)
    assert public["model_info"]["supports_vision"] is True
