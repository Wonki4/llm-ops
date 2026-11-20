"""Saved-cluster probes, credential refresh and mutation lock contention.

Opt-in PostgreSQL checks use CLUSTER_TEST_DATABASE_URL (a disposable *_test DB).
Only randomly named rows created by this fixture are removed.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi import HTTPException
from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import k8s_clusters as api
from app.clients import k8s
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.services import clusters, crypto, oidc_kubeconfig


def token(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def config(id_token=None):
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "target", "cluster": {"server": "https://target.example.test"}}],
        "contexts": [{"name": "chosen", "context": {"cluster": "target", "user": "oidc-user"}}],
        "users": [
            {
                "name": "oidc-user",
                "user": {
                    "auth-provider": {
                        "name": "oidc",
                        "config": {
                            "client-id": "public",
                            "idp-issuer-url": "https://idp.example.test/realms/test",
                            "id-token": id_token or token(int(time.time()) + 3600),
                            "refresh-token": "test-refresh",
                        },
                    }
                },
            }
        ],
    }


@pytest.fixture
async def registry(monkeypatch):
    url = os.getenv("CLUSTER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CLUSTER_TEST_DATABASE_URL for PostgreSQL lock checks")
    assert make_url(url).database.endswith("_test"), "Use an isolated test database"
    engine = create_async_engine(url, pool_size=5, max_overflow=0, connect_args={"timeout": 5, "command_timeout": 10})
    # Exclude initial TCP/TLS setup from the short lock-contention assertions.
    async with AsyncExitStack() as stack:
        for _ in range(4):
            conn = await stack.enter_async_context(engine.connect())
            await conn.execute(text("SELECT 1"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(clusters, "async_session_factory", factory)
    cid = uuid.uuid4()
    raw = config()
    async with factory() as db:
        db.add(
            CustomK8sCluster(
                id=cid,
                name=f"connection-test-{cid}",
                context="chosen",
                namespace="serving",
                kubeconfig_encrypted=crypto.encrypt(yaml.safe_dump(raw)),
            )
        )
        await db.commit()
    try:
        yield factory, cid, raw
    finally:
        async with factory() as db:
            await db.execute(delete(CustomK8sCluster).where(CustomK8sCluster.id == cid))
            await db.commit()
        await engine.dispose()


async def test_valid_saved_oidc_does_not_wait_for_unrelated_row_lock(registry, super_user, monkeypatch):
    factory, cid, raw = registry
    observed = []

    def version_api(client):
        observed.append((client.configuration.host, client.configuration.api_key["BearerToken"]))
        return SimpleNamespace(get_code=AsyncMock(return_value=SimpleNamespace(git_version="v-test")))

    monkeypatch.setattr(k8s.client, "VersionApi", version_api)
    # A pending edit or another refresh must not block a still-valid token's probe.
    async with factory() as owner:
        await owner.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cid).with_for_update())
        unsaved = await api.test_unsaved_cluster(
            api.TestClusterRequest(kubeconfig=yaml.safe_dump(raw), context="chosen"), super_user
        )
        assert unsaved["ok"]
        async with factory() as reader:
            saved = await asyncio.wait_for(api.test_saved_cluster(str(cid), super_user, reader), timeout=1)
        assert saved["ok"]
    assert len(observed) == 2 and observed[0] == observed[1]


async def expire_stored_token(factory, cid, raw):
    raw["users"][0]["user"]["auth-provider"]["config"]["id-token"] = token(0)
    async with factory() as db:
        row = await db.get(CustomK8sCluster, cid)
        row.kubeconfig_encrypted = crypto.encrypt(yaml.safe_dump(raw))
        await db.commit()


async def test_concurrent_refresh_reuses_rotated_token(registry, monkeypatch):
    factory, cid, raw = registry
    await expire_stored_token(factory, cid, raw)
    entered, release, lock_requested = asyncio.Event(), asyncio.Event(), asyncio.Event()
    new_token = token(int(time.time()) + 7200)
    calls = []

    async def refresh(fields):
        calls.append(fields["refresh-token"])
        entered.set()
        await release.wait()
        fields.update({"id-token": new_token, "refresh-token": "rotated-refresh"})

    def before_cursor(conn, cursor, statement, parameters, context, executemany):
        if "FOR NO KEY UPDATE" in statement:
            lock_requested.set()

    monkeypatch.setattr(oidc_kubeconfig, "_refresh_token", refresh)
    engine = factory.kw["bind"]
    async with asyncio.TaskGroup() as tasks:
        first = tasks.create_task(clusters._registered_oidc_kubeconfig(cid))
        await asyncio.wait_for(entered.wait(), 2)
        event.listen(engine.sync_engine, "before_cursor_execute", before_cursor)
        try:
            second = tasks.create_task(clusters._registered_oidc_kubeconfig(cid))
            await asyncio.wait_for(lock_requested.wait(), 2)
        finally:
            release.set()
            event.remove(engine.sync_engine, "before_cursor_execute", before_cursor)
    assert first.result() == second.result()
    assert calls == ["test-refresh"]
    async with factory() as db:
        row = await db.get(CustomK8sCluster, cid)
        stored = yaml.safe_load(crypto.decrypt(row.kubeconfig_encrypted))
    fields = stored["users"][0]["user"]["auth-provider"]["config"]
    assert fields["id-token"] == new_token
    assert fields["refresh-token"] == "rotated-refresh"


async def test_expired_token_lock_wait_is_bounded(registry, monkeypatch):
    factory, cid, raw = registry
    await expire_stored_token(factory, cid, raw)
    monkeypatch.setattr(clusters, "_OIDC_LOCK_TIMEOUT_SECONDS", 0.1)
    refresh = AsyncMock()
    monkeypatch.setattr(oidc_kubeconfig, "_refresh_token", refresh)
    async with factory() as owner:
        await owner.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cid).with_for_update())
        with pytest.raises(oidc_kubeconfig.OidcKubeconfigError, match="credentials lock"):
            await asyncio.wait_for(clusters._registered_oidc_kubeconfig(cid), 2)
    refresh.assert_not_awaited()


@pytest.mark.parametrize("cancel", [False, True], ids=["refresh-timeout", "request-cancelled"])
async def test_interrupted_refresh_releases_lock_for_delete(registry, super_user, monkeypatch, cancel):
    factory, cid, raw = registry
    await expire_stored_token(factory, cid, raw)
    entered = asyncio.Event()

    async def stall(fields):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(oidc_kubeconfig, "_refresh_token", stall)
    monkeypatch.setattr(oidc_kubeconfig, "_REFRESH_TIMEOUT_SECONDS", 0.1 if not cancel else 15)
    task = asyncio.create_task(clusters._registered_oidc_kubeconfig(cid))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(oidc_kubeconfig.OidcKubeconfigError, match="identity provider"):
                await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with factory() as db:
        result = await asyncio.wait_for(api.delete_cluster(str(cid), super_user, db), 2)
        assert result == {"ok": True}
        assert await db.get(CustomK8sCluster, cid) is None


@pytest.mark.parametrize("operation", ["delete", "update"])
async def test_mutation_returns_conflict_when_locked_then_succeeds(registry, super_user, monkeypatch, operation):
    factory, cid, _ = registry
    monkeypatch.setattr(api, "_CLUSTER_WRITE_TIMEOUT_SECONDS", 0.1)

    async def mutate(db):
        if operation == "delete":
            return await api.delete_cluster(str(cid), super_user, db)
        return await api.update_cluster(str(cid), api.UpdateClusterRequest(description="edited"), super_user, db)

    async with factory() as owner:
        await owner.execute(select(CustomK8sCluster).where(CustomK8sCluster.id == cid).with_for_update())
        async with factory() as writer:
            with pytest.raises(HTTPException) as caught:
                await asyncio.wait_for(mutate(writer), 2)
            assert caught.value.status_code == 409
            assert "credential refresh" in caught.value.detail
            # Cancellation must roll back the writer's transaction as well.
            assert await writer.get(CustomK8sCluster, cid) is not None
    monkeypatch.setattr(api, "_CLUSTER_WRITE_TIMEOUT_SECONDS", 2)
    async with factory() as writer:
        result = await mutate(writer)
        if operation == "delete":
            assert result == {"ok": True}
        else:
            assert result["description"] == "edited"


@pytest.mark.parametrize("stage", ["credentials", "api"])
async def test_probe_deadline_reports_stage_and_closes_client(monkeypatch, stage):
    monkeypatch.setattr(k8s, "_PROBE_TIMEOUT_SECONDS", 0.02)
    closed = AsyncMock()

    async def stall(*args, **kwargs):
        await asyncio.Event().wait()

    probe = k8s.K8sClient()
    if stage == "credentials":
        monkeypatch.setattr(probe, "_api_client", stall)
    else:
        monkeypatch.setattr(probe, "_api_client", AsyncMock(return_value=SimpleNamespace(close=closed)))
        monkeypatch.setattr(k8s.client, "VersionApi", lambda c: SimpleNamespace(get_code=stall))
    expected = "loading cluster credentials" if stage == "credentials" else "Kubernetes API"
    with pytest.raises(TimeoutError, match=expected):
        await probe.probe()
    if stage == "api":
        closed.assert_awaited_once()


async def test_saved_probe_database_timeout_rolls_back(super_user, monkeypatch):
    async def stall(*args):
        await asyncio.Event().wait()

    db = SimpleNamespace(execute=stall, rollback=AsyncMock())
    monkeypatch.setattr(api, "_SAVED_PROBE_TIMEOUT_SECONDS", 0.02)
    result = await api.test_saved_cluster(str(uuid.uuid4()), super_user, db)
    assert not result["ok"]
    assert "reading the saved cluster from the database" in result["message"]
    db.rollback.assert_awaited_once()


async def test_referenced_cluster_delete_remains_conflict(super_user):
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: object())),
        delete=AsyncMock(),
        rollback=AsyncMock(),
        commit=AsyncMock(side_effect=IntegrityError("delete", {}, Exception("foreign key"))),
    )
    with pytest.raises(HTTPException) as caught:
        await api.delete_cluster(str(uuid.uuid4()), super_user, db)
    assert caught.value.status_code == 409
    assert "in use by a deployment or benchmark" in caught.value.detail
    db.rollback.assert_awaited_once()


async def test_valid_token_needs_no_write_transaction(monkeypatch):
    row = SimpleNamespace(context="chosen", kubeconfig_encrypted=crypto.encrypt(yaml.safe_dump(config())))
    db = MagicMock()
    db.__aenter__.return_value = db
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: row))
    db.commit = AsyncMock()
    monkeypatch.setattr(clusters, "async_session_factory", lambda: db)
    ready, context = await clusters._registered_oidc_kubeconfig(uuid.uuid4())
    assert context == "chosen"
    assert list(ready["users"][0]["user"]) == ["token"]
    db.commit.assert_not_awaited()
    assert len(db.execute.await_args_list) == 1
    assert "FOR UPDATE" not in str(db.execute.await_args.args[0])


@pytest.mark.parametrize("expired", [False, True], ids=["valid-token", "expired-token"])
async def test_pending_benchmark_can_load_credentials_before_commit(registry, monkeypatch, expired):
    """The request's FK KEY SHARE lock must not deadlock its credential session."""
    from app.db.models.custom_benchmark_run import CustomBenchmarkRun

    factory, cid, raw = registry
    if expired:
        await expire_stored_token(factory, cid, raw)
    new_token = token(int(time.time()) + 7200)

    async def refresh(fields):
        fields.update({"id-token": new_token, "refresh-token": "rotated-refresh"})

    refresh_mock = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(oidc_kubeconfig, "_refresh_token", refresh_mock)
    async with factory() as request_db:
        request_db.add(
            CustomBenchmarkRun(
                model_name=f"lock-test-{cid}",
                cluster_id=cid,
                tool="portal",
                kind="performance",
                params={},
                created_by="lock-test",
            )
        )
        await request_db.flush()  # Same sequence as benchmarks.create: flush, then create_job.
        ready, context = await asyncio.wait_for(clusters._registered_oidc_kubeconfig(cid), 1)
        assert context == "chosen"
        assert ready["users"][0]["user"]["token"] == (
            new_token if expired else raw["users"][0]["user"]["auth-provider"]["config"]["id-token"]
        )
        await request_db.rollback()
    assert refresh_mock.await_count == int(expired)


async def test_blank_credential_error_is_not_misreported_as_timeout(super_user, monkeypatch):
    from cryptography.fernet import InvalidToken

    cid = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=cid))),
        rollback=AsyncMock(),
    )
    monkeypatch.setattr(api, "k8s_for_cluster", AsyncMock(side_effect=InvalidToken()))
    result = await api.test_saved_cluster(str(cid), super_user, db)
    assert not result["ok"]
    assert "InvalidToken" in result["message"]
    assert "timed out" not in result["message"]
