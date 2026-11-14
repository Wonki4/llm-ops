import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.api import serving_overview
from app.api.serving_access import GrantBody, TeamGrantChanges
from app.db.session import get_litellm_db
from app.main import app
from app.services.clusters import NamespaceScope
from app.services.serving_access import require_scope, serving_scopes
from tests.test_serving_topology import registration, snapshot


def scope(cluster, ns, role="viewer"):
    return {"cluster_id": cluster, "namespace": ns, "role": role}


def test_grants_require_an_exact_namespace():
    for namespace in ("", " ", "*", "other/*", "Production"):
        with pytest.raises(ValidationError):
            GrantBody(team_id="team", cluster_id=None, namespace=namespace, role="operator")


async def test_roles_combine_per_cluster_and_namespace(mock_db, regular_user):
    other = uuid.uuid4()
    rows = [(None, "inference", None, None, None, r) for r in ("viewer", "operator", "viewer")]
    rows.append((other, "inference", "prod", True, "inference", "viewer"))
    portal, litellm = MagicMock(), AsyncMock()
    portal.all.return_value = rows
    mock_db.execute.return_value = portal
    memberships = MagicMock()
    memberships.scalars.return_value.all.return_value = ["team-a", "team-b"]
    litellm.execute.return_value = memberships
    found = await serving_scopes(regular_user, mock_db, litellm)
    assert require_scope(found, other, "inference")["role"] == "viewer"
    assert require_scope(found, None, "inference")["role"] == "operator"
    assert len(found) == 2 and found[0]["is_default"]


async def test_team_inventory_rejects_nonmembers_before_reading_grants(user_client, mock_db):
    litellm = AsyncMock()
    app.dependency_overrides[get_litellm_db] = lambda: litellm
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    litellm.execute.return_value = result
    assert (await user_client.get("/api/teams/other/serving-deployments")).status_code == 403
    statement, params = litellm.execute.await_args.args
    assert params == {"uid": "user001", "team_id": "other"}
    assert "t.team_id = :team_id" in str(statement)
    mock_db.execute.assert_not_awaited()


@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_team_inventory_returns_granted_scopes_with_and_without_models(user_client, mock_db, role):
    from sqlalchemy.dialects import postgresql

    litellm = AsyncMock()
    app.dependency_overrides[get_litellm_db] = lambda: litellm
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["team-a"]
    litellm.execute.return_value = result
    cluster_id = uuid.uuid4()
    result = MagicMock()
    result.mappings.return_value = [
        dict(scope_cluster=None, scope_namespace="inference", name=None, role=role, id=uuid.uuid4(),
             cluster_id=None, namespace="inference", model_name="worker-a", litellm_model_name="shared",
             status="Ready", replicas=2, ready_replicas=2, serving_mode="aggregated", pd_status=None,
             engine="vllm", last_synced_at=None),
        dict(scope_cluster=cluster_id, scope_namespace="inference", name="remote", role="viewer", id=None),
    ]
    mock_db.execute.return_value = result
    response = await user_client.get("/api/teams/team-a/serving-deployments")
    assert response.status_code == 200
    portal, remote = response.json()["scopes"]
    assert portal["role"] == role and portal["deployments"][0]["model_name"] == "worker-a"
    assert remote["cluster_id"] == str(cluster_id) and remote["deployments"] == []
    stmt = mock_db.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(stmt)
    assert "IS NOT DISTINCT FROM" in sql and "custom_model_deployment.namespace =" in sql
    assert "custom_serving_grant.team_id =" in sql and "team-a" in stmt.params.values()
    assert "expires_at <= LOCALTIMESTAMP" in sql


@pytest.mark.parametrize("request_path", [
    "/api/serving/deployments?namespace=other",
    "/api/serving/topology?namespace=other",
    "/api/serving/topology/workloads?namespace=other&kind=ingress&name=model",
    "/api/serving/topology?namespace=inference&cluster_id=88888888-8888-4444-8888-888888888888",
])
async def test_scope_is_checked_before_resource_lookup(user_client, mock_db, request_path):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "inference")]
    assert (await user_client.get(request_path)).status_code == 403
    mock_db.get.assert_not_awaited()
    mock_db.execute.assert_not_awaited()


@pytest.mark.parametrize("method", ["get", "put", "delete", "patch"])
async def test_team_grants_are_admin_only(user_client, method):
    url = "/api/admin/serving/grants"
    if method == "delete":
        url += "/" + str(uuid.uuid4())
    kwargs = {"json": dict(team_id="team", cluster_id=None, namespace="ns", role="operator")} if method == "put" else {}
    if method == "patch":
        kwargs["json"] = {"team_id": "team", "changes": [dict(scope(None, "ns"), previous_role=None)]}
    assert (await getattr(user_client, method)(url, **kwargs)).status_code == 403
    if method == "get":
        assert (await user_client.get(url + "/teams")).status_code == 403


def test_team_edits_require_unique_normalized_scopes():
    first = dict(scope(None, "ns"), previous_role=None)
    with pytest.raises(ValidationError):
        TeamGrantChanges(team_id="team", changes=[first, dict(first, namespace=" ns ")])
    assert len(TeamGrantChanges(team_id="team", changes=[first, dict(first, cluster_id=uuid.uuid4())]).changes) == 2


@pytest.mark.parametrize(("current_role", "expected"), [("viewer", 200), ("operator", 409)])
async def test_batch_grant_retry_or_conflict(admin_client, mock_db, current_role, expected):
    teams = AsyncMock()
    app.dependency_overrides[get_litellm_db] = lambda: teams
    mock_db.scalar.side_effect = [None, current_role]
    change = dict(scope(None, "ns"), previous_role=None)
    result = await admin_client.patch("/api/admin/serving/grants", json={"team_id": "team", "changes": [change]})
    assert result.status_code == expected
    mock_db.add.assert_not_called()


async def test_unresolvable_grant_target_is_rejected(admin_client, mock_db):
    teams = AsyncMock()
    app.dependency_overrides[get_litellm_db] = lambda: teams
    body = dict(team_id="team", cluster_id=str(uuid.uuid4()), namespace="ns", role="viewer")
    mock_db.get.return_value = None
    assert (await admin_client.put("/api/admin/serving/grants", json=body)).status_code == 404
    teams.scalar.return_value = None
    body["cluster_id"] = None
    assert (await admin_client.put("/api/admin/serving/grants", json=body)).status_code == 404
    mock_db.add.assert_not_called()


async def test_team_graph_uses_owned_registration_ids_and_queries_only_the_permitted_scope(monkeypatch, mock_db):
    dep = SimpleNamespace(model_name="shared", litellm_model_name="shared", id=uuid.uuid4(),
                          litellm_model_id="ours", cluster_id=None, namespace="ns")
    permitted = NamespaceScope(None, "ns")
    db_results = []
    for rows in ([dep], [], []):
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        db_results.append(result)
    mock_db.execute.side_effect = db_results
    routes = AsyncMock(return_value=snapshot())
    monkeypatch.setattr(serving_overview.route_snapshots, "read", routes)
    # Two registrations using the same alias and endpoint are owned by different scopes.
    models = [registration("shared", mid, "https://models.test/chat") for mid in ("ours", "foreign")]
    models.append(registration("unowned-cloud", "cloud", None))
    proxy = SimpleNamespace(get_model_info=AsyncMock(return_value=models))
    graph = await serving_overview.load_topology(mock_db, proxy, "default", "ns", True, permitted=permitted)
    assert {n["label"] for n in graph["nodes"] if n["kind"] == "registration"} == {"ours"}
    assert graph["scopes"] == [{"cluster_id": None, "namespace": "ns"}]
    assert "unowned-cloud" not in str(graph)
    routes.assert_awaited_once_with(permitted)
    for call in mock_db.execute.await_args_list:
        stmt = str(call.args[0])
        assert "cluster_id IS NULL" in stmt and "namespace =" in stmt
