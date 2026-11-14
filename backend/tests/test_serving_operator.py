"""Operator API boundaries and the shared deployment paths."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.api import model_deployments as deployments
from app.main import app
from app.services.serving_access import serving_scopes
from tests.test_deployment_reapply import _recipe, _row
from tests.test_serving_access import scope

BASE = "/api/serving/deployments"
RID = "11111111-1111-4111-8111-111111111111"
BODY = {"cluster_id": None, "namespace": "team-dev", "recipe_id": RID, "model_name": "new"}


@pytest.mark.parametrize(("method", "url", "payload"), [
    ("GET", "/api/serving/recipes?namespace=team-dev", None),
    ("POST", BASE, BODY), ("PUT", f"{BASE}/{RID}/replicas", {"replicas": 0}),
    ("PUT", f"{BASE}/{RID}/pools/decode/replicas", {"replicas": 3}),
    ("POST", f"{BASE}/{RID}/reapply", {"recipe_id": RID}), ("DELETE", f"{BASE}/{RID}", None),
])
async def test_viewer_has_no_operator_endpoints(user_client, mock_db, method, url, payload):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "team-dev", "viewer")]
    result = await user_client.request(method, url, **({"json": payload} if payload else {}))
    assert result.status_code == 403
    mock_db.get.assert_not_called()
    mock_db.scalar.assert_not_called()


@pytest.mark.parametrize("target", [dict(namespace="elsewhere"), dict(cluster_id=RID)])
async def test_operator_must_own_exact_create_scope(user_client, mock_db, target):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "team-dev", "operator")]
    assert (await user_client.post(BASE, json=BODY | target)).status_code == 403
    mock_db.get.assert_not_called()


@pytest.mark.parametrize("extra", [dict(image="mine"), dict(env={"X": "Y"}), dict(router_stack_id=RID),
                                   dict(litellm_model_name="shared"), dict(ingress_host="another.test")])
async def test_operator_cannot_override_recipe_or_routing(user_client, mock_db, extra):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "team-dev", "operator")]
    assert (await user_client.post(BASE, json=BODY | extra)).status_code == 422
    mock_db.get.assert_not_called()


@pytest.mark.parametrize("mode", ["pd", "aggregated"])
async def test_operator_deploy_builds_from_current_admin_recipe(user_client, mock_db, monkeypatch, mode):
    recipe = _recipe(serving_mode=mode, pd_config={"prefill": {}, "decode": {}} if mode == "pd" else None)
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "team-dev", "operator")]
    mock_db.get.return_value = recipe
    mock_db.scalar.return_value = None
    create = AsyncMock(return_value={"id": RID, "env": {"internal": "value"}})
    monkeypatch.setattr(deployments, "create_deployment", create)
    counts = {"prefill_replicas": 3, "decode_replicas": 2} if mode == "pd" else {"replicas": 2}
    response = await user_client.post(BASE, json=BODY | counts)
    assert response.status_code == 201, response.text
    assert response.json() == {"id": RID}
    body, user, _ = create.await_args.args
    assert user.user_id == "user001"
    assert body.namespace == "team-dev" and body.cluster_id is None
    assert (body.engine_args, body.image, body.env) == (recipe.engine_args, recipe.image, recipe.env)
    assert body.recipe_id == str(recipe.id) and body.replicas == 2 and body.litellm_api_base is None
    if mode == "pd":
        assert body.pd_config["prefill"]["replicas"] == 3


async def test_existing_litellm_alias_is_rejected(user_client, mock_db, mock_litellm, monkeypatch):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "team-dev", "operator")]
    mock_db.get.return_value = _recipe()
    mock_db.scalar.return_value = None
    mock_litellm.get_model_info.return_value = [{"model_name": "new"}]
    create = AsyncMock()
    monkeypatch.setattr(deployments, "create_deployment", create)
    assert (await user_client.post(BASE, json=BODY)).status_code == 409
    create.assert_not_called()


@pytest.mark.parametrize(("method", "suffix", "payload"), [
    ("PUT", "/replicas", {"replicas": 2}), ("PUT", "/pools/prefill/replicas", {"replicas": 2}),
    ("POST", "/reapply", {"recipe_id": RID}), ("DELETE", "", None),
])
async def test_foreign_deployment_is_excluded_from_locked_lookup(user_client, mock_db, method, suffix, payload):
    cid = uuid.uuid4()
    app.dependency_overrides[serving_scopes] = lambda: [scope(cid, "edit-ns", "operator"), scope(None, "view-ns")]
    mock_db.scalar.return_value = None
    r = await user_client.request(method, f"{BASE}/{RID}{suffix}", **({"json": payload} if payload else {}))
    assert r.status_code == 404
    stmt = mock_db.scalar.await_args.args[0]
    assert stmt._for_update_arg is not None
    assert {"edit-ns", cid, uuid.UUID(RID)} == set(stmt.compile().params.values())
    mock_db.delete.assert_not_called()


async def test_delete_uses_only_scoped_registration(user_client, mock_db, mock_litellm, monkeypatch):
    app.dependency_overrides[serving_scopes] = lambda: [scope(None, "ns", "operator")]
    dep = _row(litellm_model_id="own-id")
    mock_db.scalar.return_value = dep
    k8s = AsyncMock()
    monkeypatch.setattr(deployments, "k8s_for_cluster", AsyncMock(return_value=k8s))
    assert (await user_client.delete(f"{BASE}/{dep.id}")).status_code == 200
    k8s.delete.assert_awaited_once()
    mock_litellm.delete_model.assert_awaited_once_with("own-id")
