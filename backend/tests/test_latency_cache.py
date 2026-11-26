import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.custom_latency_cache import CustomLatencyCache
from app.db.models.custom_model_catalog import CustomModelCatalog
from app.db.session import get_db
from app.main import app
from app.services import latency_cache as cache
from app.services import model_live_latency as latency
from app.services.cluster_prometheus import PrometheusConnection, invalidate_latency_routes
from app.services.clusters import NamespaceScope


def test_common_evaluation_grid():
    assert [cache.latency_tick(t, history=False) for t in (1799.99, 1800, 1829.99)] == [1785, 1800, 1815]
    assert [cache.latency_tick(t, history=True) for t in (1799.99, 1800, 1829.99)] == [1770, 1800, 1800]


@pytest.fixture
async def sessions():
    url = os.environ.get("CLUSTER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Use the migrated, isolated cluster test database for cross-worker checks")
    assert make_url(url).database.endswith("_test")
    # Even the request paths that resolve targets in this pool must leave the
    # one connection available to other viewers and independent cache leases.
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        yield async_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("history", [True, False])
async def test_concurrent_viewers_across_workers_issue_one_fetch(sessions, monkeypatch, history):
    workers = [cache.LatencyCache(), cache.LatencyCache()]
    scope = {"cluster": str(uuid.uuid4()), "model": "shared/view"}
    contender = asyncio.Event()
    real_lookup = cache._lookup

    async def lookup(*args):
        result = await real_lookup(*args)
        if result == (None, False):
            contender.set()
        return result

    monkeypatch.setattr(cache, "_lookup", lookup)

    async def fetch():
        await asyncio.wait_for(contender.wait(), timeout=3)
        return {"points": [0, None, 32]}

    loader = AsyncMock(side_effect=fetch)
    options = dict(scope=scope, history=history, now=1800, load=loader)

    async def read(worker):
        async with sessions() as session:
            return await worker.read(session, **options)

    values = await asyncio.gather(*(read(workers[i % 2]) for i in range(20)))
    assert values == [{"points": [0, None, 32]}] * 20
    # A fresh worker can serve the completed query too.
    assert await read(cache.LatencyCache()) == values[0]
    loader.assert_awaited_once()
    async with sessions() as db:
        for change in ({"scope": {**scope, "cluster": str(uuid.uuid4())}}, {"history": not history}, {"now": 1830}):
            await workers[0].read(db, **(options | change))
        assert loader.await_count == 4


async def test_failed_cache_store_keeps_local_deduplication(monkeypatch):
    engine = create_async_engine("postgresql+asyncpg://unused.invalid/unused")
    monkeypatch.setattr(cache, "_lookup", AsyncMock(side_effect=TimeoutError()))
    worker = cache.LatencyCache()
    fetch = AsyncMock(return_value={"ttft": {"p50_ms": 12}})
    try:
        async with async_sessionmaker(engine)() as db:
            values = await asyncio.gather(
                *(worker.read(db, scope={"c": "east"}, history=False, now=1800, load=fetch) for _ in range(8))
            )
        assert values == [{"ttft": {"p50_ms": 12}}] * 8
        fetch.assert_awaited_once()
    finally:
        await engine.dispose()


async def test_sessions_without_postgresql_never_reuse_shared_state(mock_db):
    worker = cache.LatencyCache()
    load = AsyncMock(side_effect=[{"p50": 1}, {"p50": 2}])
    options = dict(scope={"model": "same"}, history=False, now=1800, load=load)
    assert await worker.read(mock_db, **options) == {"p50": 1}
    assert await worker.read(mock_db, **options) == {"p50": 2}
    assert not worker.recent and not worker.pending


async def test_concurrent_viewers_share_an_oversized_result_without_retaining_it(sessions):
    worker = cache.LatencyCache(max_result_bytes=32)
    payload = {"targets": ["example-serving-pod"] * 8}
    started = asyncio.Event()
    resume = asyncio.Event()

    async def load():
        started.set()
        await resume.wait()
        return payload

    reader = AsyncMock(side_effect=load)
    options = dict(scope={"model": uuid.uuid4().hex}, history=False, now=1800, load=reader)
    async with sessions() as db:
        first = asyncio.create_task(worker.read(db, **options))
        await asyncio.wait_for(started.wait(), 3)
        waiter = asyncio.create_task(worker.read(db, **options))
        await asyncio.sleep(0)
        resume.set()
        assert await asyncio.gather(first, waiter) == [payload, payload]
        reader.assert_awaited_once()
        assert not worker.recent
        assert await worker.read(db, **options) == payload
        assert reader.await_count == 2


async def test_failed_or_abandoned_fetch_can_be_retried(sessions):
    key = uuid.uuid4().hex
    first, second = uuid.uuid4(), uuid.uuid4()
    assert await cache._lookup(sessions, key, first) == (None, True)
    assert await cache._lookup(sessions, key, second) == (None, False)
    async with sessions.begin() as db:
        await db.execute(update(CustomLatencyCache).where(CustomLatencyCache.key == key).values(expires_at=func.now()))
    assert await cache._lookup(sessions, key, second) == (None, True)
    await cache._finish(sessions, key, first, {"old": True}, 15)
    assert await cache._lookup(sessions, key, first) == (None, False)
    await cache._finish(sessions, key, second, None, 15)
    async with sessions() as db:
        assert await db.scalar(select(CustomLatencyCache.key).where(CustomLatencyCache.key == key)) is None


async def test_long_discovery_keeps_its_lease_across_workers(sessions, monkeypatch):
    monkeypatch.setattr(cache, "_RENEW_SECONDS", 0.05)
    claimed_key = asyncio.get_running_loop().create_future()
    waiting, finish = asyncio.Event(), asyncio.Event()
    lookup = cache._lookup

    async def observe(factory, key, owner):
        result, claimed = await lookup(factory, key, owner)
        if claimed and not claimed_key.done():
            claimed_key.set_result(key)
        if result is None and not claimed:
            waiting.set()
        return result, claimed

    monkeypatch.setattr(cache, "_lookup", observe)

    async def load():
        await asyncio.wait_for(finish.wait(), 10)
        return {"shared": True}

    reader = AsyncMock(side_effect=load)
    options = dict(scope={"model": uuid.uuid4().hex}, history=False, now=1800, load=reader)
    async with sessions() as db:
        first = asyncio.create_task(cache.LatencyCache().read(db, **options))
        key = await asyncio.wait_for(claimed_key, 5)
        scope = CustomLatencyCache.key == key
        async with sessions.begin() as conn:
            await conn.execute(update(CustomLatencyCache).where(scope).values(expires_at=func.now()))
        # Wait for evidence of an actual renewal before admitting the next worker.
        async with asyncio.timeout(5):
            while True:
                async with sessions() as conn:
                    if await conn.scalar(select(CustomLatencyCache.expires_at > func.now()).where(scope)):
                        break
                await asyncio.sleep(0.02)
        other = asyncio.create_task(cache.LatencyCache().read(db, **options))
        await asyncio.wait_for(waiting.wait(), 5)
        finish.set()
        assert await asyncio.gather(first, other) == [{"shared": True}] * 2
        reader.assert_awaited_once()


async def test_cancelled_reader_releases_lease_and_wakes_waiter(sessions):
    worker = cache.LatencyCache()
    started = asyncio.Event()
    waiting = asyncio.Event()

    async def blocked():
        started.set()
        await waiting.wait()

    async with sessions() as db:
        options = dict(scope={"model": str(uuid.uuid4())}, history=True, now=1800)
        reader = asyncio.create_task(worker.read(db, load=blocked, **options))
        await asyncio.wait_for(started.wait(), timeout=3)
        new_result = {"points": [11]}
        waiter = asyncio.create_task(worker.read(db, load=AsyncMock(return_value=new_result), **options))
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert await asyncio.wait_for(waiter, timeout=3) == new_result
        assert not worker.pending


async def test_api_shared_result_rechecks_visibility_targets_and_access(
    sessions, client_for_user, regular_user, super_user, mock_litellm, monkeypatch,
):
    model = "scope-test-" + uuid.uuid4().hex
    path = f"/api/models/{model}/live-latency"
    mock_litellm.get_model_info.return_value = [{
        "model_name": model, "model_info": {"id": model},
        "litellm_params": {"api_base": "http://server.serving.svc/v1", "model": "openai/chat"},
    }]
    tick = 1800
    monkeypatch.setattr(latency, "latency_tick", lambda *args, **kwargs: tick)
    monkeypatch.setattr(latency, "namespace_scopes", AsyncMock(return_value=[NamespaceScope(None, "serving")]))
    routes = {"paths": [], "services": {"server": {"app": "chat"}}}
    monkeypatch.setattr(latency.route_snapshots, "read", AsyncMock(return_value=routes))
    pods = {"pod_owners": {}, "pod_engines": {"p1": "sglang"}, "pods": [
        ("p1", {"app": "chat"}, "Running", True, False)
    ]}
    monkeypatch.setattr(latency.workload_snapshots, "read", AsyncMock(return_value=pods))
    resolver = AsyncMock(return_value=PrometheusConnection("https://central", True, "cluster", "east", "test-token"))
    monkeypatch.setattr(latency, "resolve_prometheus_connection", resolver)

    async def sample(client, url, *, cluster_label, pods, **kwargs):
        assert sessions.kw["bind"].pool.checkedout() == 0
        return {"status": "ok", "metrics": {"ttft": {"p50_ms": len(pods), "status": "ok"}}}

    reader = AsyncMock(side_effect=sample)
    monkeypatch.setattr(latency, "read_latency", reader)

    async def session():
        async with sessions() as db:
            yield db

    async def fetch(user):
        # Each request can hit a different worker. A cache hit here must be in PostgreSQL.
        monkeypatch.setattr(latency, "cached_latency", cache.LatencyCache().read)
        async with client_for_user(user) as client:
            app.dependency_overrides[get_db] = session
            return await client.get(path)

    async with sessions.begin() as db:
        db.add(CustomModelCatalog(model_name=model, display_name=model, visible=True))
    try:
        admin = await fetch(super_user)
        viewer = await fetch(regular_user)
        assert viewer.status_code == admin.status_code == 200
        assert viewer.json() == admin.json() and viewer.json()["rows"][0]["metrics"]["ttft"]["p50_ms"] == 1
        reader.assert_awaited_once()
        # A saved credential edit invalidates all workers in the same tick.
        resolver.return_value = PrometheusConnection("https://central", True, "cluster", "east", "changed-token")
        async with sessions.begin() as db:
            await invalidate_latency_routes(db)
        assert (await fetch(regular_user)).status_code == 200 and reader.await_count == 2
        pods["pods"].append(("p2", {"app": "chat"}, "Running", True, False))
        pods["pod_engines"]["p2"] = "sglang"
        assert (await fetch(regular_user)).json()["rows"][0]["metrics"]["ttft"]["p50_ms"] == 1
        tick += 15
        assert (await fetch(regular_user)).json()["rows"][0]["metrics"]["ttft"]["p50_ms"] == 2
        async with sessions.begin() as db:
            await db.execute(update(CustomModelCatalog).where(CustomModelCatalog.model_name == model).values(
                visible=False,
            ))
        assert (await fetch(regular_user)).status_code == 404
        assert reader.await_count == 3
    finally:
        async with sessions.begin() as db:
            await db.execute(delete(CustomModelCatalog).where(CustomModelCatalog.model_name == model))
