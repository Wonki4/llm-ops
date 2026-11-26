import json
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import delete, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import models_catalog
from app.clients.litellm import LiteLLMClient
from app.config import settings
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.session import get_db, get_litellm_db
from app.main import app
from app.services import crypto, latency_inventory, model_live_latency
from app.services.latency_cache import LatencyCache
from app.services.model_registry import public_model_info


async def test_public_and_private_fields_are_separated_in_shared_inventory(monkeypatch, mock_litellm):
    params = {
        "api_base": "https://host.test/private-path/v1?key=secret-query#secret-fragment",
        "api_key": "secret-key", "model": "openai/upstream", "extra_headers": {"Auth": "secret-header"},
        "input_cost_per_token": 0, "weight": 2,
    }
    info = {"id": "id", "credentials": "secret-info", "access_groups": ["alpha"], "supports_vision": True,
            "supports_unknown": "secret-extra", "db_model": False, "input_cost_per_token": 3,
            "output_cost_per_token": 4}
    mock_litellm.get_model_info.return_value = [
        {"model_name": "alias", "model_info": info, "litellm_params": params},
        {"model_name": "bad", "litellm_params": {**params, "api_base": "https://me:secret-userinfo@host.test/"}},
        {"model_name": "cloud", "litellm_params": {"model": "openai/cloud"}},
    ]

    async def read(db, *, scope, history, now, load):
        payload = await load()
        assert "secret" not in json.dumps([scope, payload]) and "private-path" not in json.dumps(payload)
        assert "secret" not in crypto.decrypt(payload["inventory"])
        return payload

    monkeypatch.setattr(latency_inventory._cache, "read", read)
    rows = await latency_inventory.read_latency_inventory(None, mock_litellm, now=1800)
    assert [r["is_cloud"] for r in rows] == [False, False, True]
    assert [r["litellm_params"]["api_base"] for r in rows] == [
        "https://host.test/private-path/v1", None, None,
    ]
    public = public_model_info(rows[0])
    assert public == public_model_info(mock_litellm.get_model_info.return_value[0]) == {
        "model_name": "alias", "litellm_params": {"model": "openai/upstream", "weight": 2},
        "model_info": {"id": "id", "db_model": False, "access_groups": ["alpha"], "supports_vision": True,
                       "input_cost_per_token": 0, "output_cost_per_token": 4, "cache_read_input_token_cost": None},
    }
    assert "host.test" not in json.dumps(public)


def test_inventory_is_scoped_to_upstream_credentials_and_encryption_configuration(monkeypatch):
    initial = latency_inventory.inventory_source(LiteLLMClient())
    assert initial == latency_inventory.inventory_source(LiteLLMClient())
    monkeypatch.setattr(settings, "litellm_base_url", "https://another-proxy.test")
    second = latency_inventory.inventory_source(LiteLLMClient())
    monkeypatch.setattr(settings, "litellm_admin_api_key", "another-key")
    third = latency_inventory.inventory_source(LiteLLMClient())
    monkeypatch.setattr(settings, "encryption_key", "another-storage-key")
    assert len({initial, second, third, latency_inventory.inventory_source(LiteLLMClient())}) == 4


@pytest.fixture
async def sessions():
    url = os.environ.get("CLUSTER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Shared inventory verification requires the isolated cluster test database")
    assert make_url(url).database.endswith("_test")
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_different_workers_share_inventory_and_retry_failure_next_tick(monkeypatch, mock_litellm, sessions):
    mock_litellm.inventory_namespace = uuid.uuid4().hex
    loader = AsyncMock(side_effect=[
        [{"model_name": "first"}, {"model_name": "second"}],
        httpx.ConnectTimeout("test failure"), [{"model_name": "updated"}],
    ])
    mock_litellm.get_model_info = loader

    async def read(tick):
        # Every read starts on a fresh worker with an empty process-local cache.
        monkeypatch.setattr(latency_inventory, "_cache", LatencyCache())
        async with sessions() as db:
            return await latency_inventory.read_latency_inventory(db, mock_litellm, now=tick)

    assert [r["model_name"] for r in await read(1800)] == ["first", "second"]
    assert [r["model_name"] for r in await read(1814)] == ["first", "second"]
    assert loader.await_count == 1
    for tick in (1815, 1829):
        with pytest.raises(httpx.RequestError, match="routing information unavailable"):
            await read(tick)
    assert loader.await_count == 2
    assert [r["model_name"] for r in await read(1830)] == ["updated"]
    assert loader.await_count == 3


async def test_model_list_summary_and_latency_share_inventory_but_recheck_visibility(
    sessions, user_client, mock_litellm, monkeypatch,
):
    model = f"inventory-{uuid.uuid4().hex}"
    now = SimpleNamespace(time=lambda: 1800)
    monkeypatch.setattr(latency_inventory, "time", now)
    monkeypatch.setattr(model_live_latency, "time", now)
    monkeypatch.setattr(models_catalog, "time", now)

    async def session():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = app.dependency_overrides[get_litellm_db] = session

    async def load():
        assert sessions.kw["bind"].pool.checkedout() == 0
        return [{
            "model_name": model,
            "model_info": {"id": "reg", "access_groups": ["team-group"], "input_cost_per_token": 5},
            "litellm_params": {"model": "openai/shared", "input_cost_per_token": 0, "api_key": "secret-token",
                               "api_base": "https://api.openai.com/private-serving-path?token=secret-query"},
        }]

    mock_litellm.get_model_info.side_effect = load
    async with sessions.begin() as db:
        db.add(CustomModelCatalog(model_name=model, display_name=model, visible=True))
    try:
        listed = await user_client.get("/api/models")
        assert listed.status_code == 200
        assert listed.headers["Cache-Control"] == "private, no-store"
        entry = next(r for r in listed.json()["models"] if r["model_name"] == model)
        summary = await user_client.get(f"/api/models/{model}/summary")
        assert summary.headers["Cache-Control"] == "private, no-store"
        assert summary.status_code == 200
        assert summary.json()["litellm_info"] == entry["litellm_info"]
        assert entry["litellm_info"]["model_info"]["access_groups"] == ["team-group"]
        assert entry["litellm_info"]["model_info"]["input_cost_per_token"] == 0
        for private in ("private-serving-path", "secret-token", "secret-query", "is_cloud"):
            assert private not in summary.text and private not in listed.text
        for suffix in ("live-latency", "live-latency/history"):
            response = await user_client.get(f"/api/models/{model}/{suffix}")
            assert response.status_code == 200 and response.json()["unmapped_count"] == 1
        mock_litellm.get_model_info.assert_awaited_once()

        async with sessions.begin() as db:
            await db.execute(update(CustomModelCatalog).where(CustomModelCatalog.model_name == model)
                             .values(visible=False))
        for suffix in ("summary", "live-latency", "live-latency/history"):
            assert (await user_client.get(f"/api/models/{model}/{suffix}")).status_code == 404
        visible = (await user_client.get("/api/models")).json()["models"]
        assert all(r["model_name"] != model for r in visible)
        mock_litellm.get_model_info.assert_awaited_once()
    finally:
        async with sessions.begin() as db:
            await db.execute(delete(CustomModelCatalog).where(CustomModelCatalog.model_name == model))
