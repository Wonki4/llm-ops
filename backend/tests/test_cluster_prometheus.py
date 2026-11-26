import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api import k8s_clusters as api
from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.session import get_db
from app.main import app
from app.services import cluster_prometheus as prom
from app.services import crypto
from tests.test_cluster_connection_lifecycle import registry as registry  # noqa: F401
from tests.test_k8s_clusters import SAMPLE_KUBECONFIG

API = "/api/admin/k8s-clusters"


def fields(url="https://central.example.test/prom", label="cluster", value="gpu-a", **updates):
    return (
        dict(prometheus_url=url, prometheus_shared=True, prometheus_cluster_label=label, prometheus_cluster_value=value)
        | updates
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"prometheus_cluster_label": None},
        {"prometheus_cluster_value": " "},
        {"prometheus_cluster_label": "__name__"},
        {"prometheus_cluster_label": "model_name"},
        {"prometheus_cluster_label": "pod"},
        {"prometheus_cluster_label": "portal_latency_kind"},
        {"prometheus_cluster_label": "le"},
        {"prometheus_cluster_label": "bad-name"},
        {"prometheus_cluster_value": "bad\nvalue"},
        {"prometheus_url": "https://user:pass@example.test/prom"},
        {"prometheus_url": "https://p/?query=secret"},
        {"prometheus_url": "file:///metrics"},
        {"prometheus_url": "https://central.test:70000"},
        {"prometheus_bearer_token": "bad\r\ntoken"},
    ],
)
async def test_invalid_configuration_is_rejected_before_using_prometheus(updates, admin_client, monkeypatch):
    probe = AsyncMock()
    monkeypatch.setattr(api, "probe_prometheus", probe)
    response = await admin_client.post(API + "/prometheus/test", json=fields(**updates))
    assert response.status_code == 400
    assert "secret" not in response.text and "user:pass" not in response.text
    probe.assert_not_awaited()


async def test_non_admin_cannot_probe_or_register(user_client):
    body = fields()
    assert (await user_client.post(API + "/prometheus/test", json=body)).status_code == 403
    assert (await user_client.put(API + "/" + str(uuid.uuid4()), json=body)).status_code == 403


@pytest.mark.parametrize(
    ("response_code", "results", "found"),
    [
        (200, [{"metric": {}, "value": [0, "10"]}], True),
        (200, [], False),
        (403, [], None),
    ],
)
async def test_probe_distinguishes_no_series_and_api_error(monkeypatch, response_code, results, found):
    conn = await prom.proposed_connection(
        AsyncMock(), prom.PrometheusFields(**fields(value='gpu-"a"', prometheus_bearer_token="test-only")), None
    )
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            response_code, json={"status": "success", "data": {"resultType": "vector", "result": results}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(prom.httpx, "AsyncClient", lambda **kwargs: client)
    result = await prom.probe_prometheus(conn)
    assert result["ok"] is (response_code == 200)
    assert result["series_found"] is found
    assert len(seen) == 1
    assert seen[0].url.path == "/prom/api/v1/query"
    assert 'cluster="gpu-\\"a\\""' in seen[0].url.params["query"]
    assert seen[0].headers["Authorization"] == "Bearer test-only"
    assert "test-only" not in str(result)


@pytest.mark.parametrize(("label", "value"), [(None, None), ("region", "other"), ("cluster", "gpu-a")])
async def test_invalid_central_scope_blocks_reads_and_edits(label, value):
    row = SimpleNamespace(id=uuid.uuid4(), **fields(), prometheus_token_encrypted=None)
    db = AsyncMock()
    db.get.return_value = row
    db.execute.return_value = [SimpleNamespace(**fields(label=label, value=value, prometheus_shared=False))]
    with pytest.raises(prom.PrometheusConfigError):
        await prom.resolve_prometheus_connection(db, row.id)
    with pytest.raises(prom.PrometheusConfigError):
        await prom.proposed_connection(db, prom.PrometheusFields(), row)


async def test_portal_scope_uses_environment_independently_of_registered_default(monkeypatch):
    for name, value in fields(value="portal").items():
        monkeypatch.setattr(settings, name, value)
    db = AsyncMock()
    registered = SimpleNamespace(id=uuid.uuid4(), is_default=True, **fields(), prometheus_token_encrypted=None)
    db.execute.return_value = [registered]
    assert (await prom.resolve_prometheus_connection(db, None)).cluster_label == ("cluster", "portal")
    db.get.assert_not_awaited()
    db.get.return_value = registered
    db.execute.return_value = []
    assert (await prom.resolve_prometheus_connection(db, registered.id)).cluster_label == ("cluster", "gpu-a")
    monkeypatch.setattr(settings, "prometheus_cluster_value", "gpu-a")
    with pytest.raises(prom.PrometheusConfigError):
        await prom.resolve_prometheus_connection(db, registered.id)


@pytest.fixture
async def db_client(registry, admin_client):
    factory, cid, _ = registry

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return admin_client, factory, cid


async def test_create_edit_probe_remove_and_secret_handling(db_client, monkeypatch):
    client, factory, _ = db_client
    base = f"https://{uuid.uuid4()}.example.test/query"
    body = fields(url=base.upper().replace("/QUERY", "/query") + "/", prometheus_bearer_token="read-secret")
    response = await client.post(
        API,
        json={
            "name": str(uuid.uuid4()),
            "context": "prod",
            "kubeconfig": SAMPLE_KUBECONFIG,
            **body,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    cid = result["id"]
    try:
        assert result["prometheus_has_token"] and result["prometheus_url"] == base
        assert "read-secret" not in response.text and "prometheus_bearer_token" not in result
        original = None
        async with factory() as db:
            row = await db.get(CustomK8sCluster, uuid.UUID(cid))
            original = row.prometheus_token_encrypted
            assert original != "read-secret" and crypto.decrypt(original) == "read-secret"
        listed = await client.get(API)
        assert "read-secret" not in listed.text
        updated = await client.put(f"{API}/{cid}", json={"prometheus_cluster_value": "gpu-b"})
        assert updated.status_code == 200
        async with factory() as db:
            conn = await prom.resolve_prometheus_connection(db, uuid.UUID(cid))
            assert conn.cluster_label == ("cluster", "gpu-b") and conn.token == "read-secret"
            assert (await db.get(CustomK8sCluster, uuid.UUID(cid))).prometheus_token_encrypted == original
        probe = AsyncMock(return_value={"ok": True, "series_found": True, "message": "ok"})
        monkeypatch.setattr(api, "probe_prometheus", probe)
        destination = {"prometheus_url": "https://different.example.test"}
        assert (await client.post(API + "/prometheus/test", json={"cluster_id": cid, **destination})).status_code == 400
        probe.assert_not_awaited()
        assert (await client.put(f"{API}/{cid}", json=destination)).status_code == 400
        assert (await client.post(API + "/prometheus/test", json={"cluster_id": cid})).json()["series_found"]
        assert probe.await_args.args[0].token == "read-secret"
        assert (await client.put(f"{API}/{cid}", json=destination | {"prometheus_bearer_token": ""})).status_code == 200
        cleared = (await client.put(f"{API}/{cid}", json={"prometheus_url": None})).json()
        assert not cleared["prometheus_has_token"] and cleared["prometheus_cluster_value"] is None
        async with factory() as db:
            assert await prom.resolve_prometheus_connection(db, uuid.UUID(cid)) is None
    finally:
        assert (await client.delete(f"{API}/{cid}")).status_code == 200


async def test_concurrent_writers_cannot_share_the_same_scope(db_client):
    client, _, cid = db_client
    url = f"https://{uuid.uuid4()}.example.test"
    second = (
        await client.post(
            API,
            json={
                "name": str(uuid.uuid4()),
                "context": "prod",
                "kubeconfig": SAMPLE_KUBECONFIG,
            },
        )
    ).json()["id"]
    try:
        ids = [str(cid), second]
        body = fields(url=url)
        responses = await asyncio.gather(*(client.put(f"{API}/{cluster}", json=body) for cluster in ids))
        assert sorted(r.status_code for r in responses) == [200, 400]
        other = ids[next(i for i, r in enumerate(responses) if r.status_code == 400)]
        assert (await client.put(f"{API}/{other}", json=fields(url=url, value="gpu-b"))).status_code == 200
        assert (
            await client.put(
                f"{API}/{other}",
                json={
                    "prometheus_shared": False,
                    "prometheus_cluster_label": None,
                    "prometheus_cluster_value": None,
                },
            )
        ).status_code == 400
    finally:
        assert (await client.delete(f"{API}/{second}")).status_code == 200
