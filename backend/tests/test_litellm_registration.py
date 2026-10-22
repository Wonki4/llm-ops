"""LiteLLM registration: the public name is configurable (and shareable); the served name is what vLLM answers to."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.litellm_registration import litellm_name, registration, served_model_name
from tests.test_storage_recipes_deployments import DEPLOY, _dep_row, _k8s, _phases, _refresh, _resolved, _result


def _agg(**kw):
    return _dep_row(**{"storages": [], **kw})


@pytest.mark.parametrize(
    ("kw", "served"),
    [
        ({}, "/models/llama"),  # vLLM's default served name is the whole --model value
        ({"engine_args": {"served-model-name": "llama-3"}}, "llama-3"),
        ({"vllm_extra_args": ["--served-model-name=llama-x"]}, "llama-x"),
        ({"vllm_extra_args": ["--served-model-name", "a", "b"]}, "a"),
        ({"engine": "sglang"}, "/models/llama"),
        ({"engine": "sglang", "engine_args": {"served-model-name": "sg"}}, "sg"),
    ],
)
def test_served_model_name(kw, served):
    assert served_model_name(_agg(**kw)) == served


def test_pd_served_name_is_the_decode_pools():
    dep = _agg(
        serving_mode="pd",
        pd_config={
            "prefill": {"replicas": 1, "engine_args": {"served-model-name": "p"}},
            "decode": {"replicas": 1, "pd_extras": {
                "args": ["--port=8200", '--kv-transfer-config={"kv_connector":"NixlConnector","kv_role":"kv_consumer"}',
                         "--served-model-name=d"],
                "env": {"VLLM_NIXL_SIDE_CHANNEL_HOST": "fieldRef:status.podIP"},
                "labels": {"llm-d.ai/role": "decode"},
            }},
        },
    )
    assert served_model_name(dep) == "d"


def test_litellm_name_defaults_to_model_name():
    assert litellm_name(_agg()) == "llama"
    assert litellm_name(_agg(litellm_model_name="meta/Llama-3")) == "meta/Llama-3"


def test_registration_arguments():
    reg = registration(_agg(litellm_model_name="meta/Llama-3", ingress_host="llama.local", ingress_path="/v1x/"))
    assert reg == {
        "model_name": "meta/Llama-3",
        "litellm_model": "openai//models/llama",
        "api_base": "https://llama.local/v1x",
        "api_key": "EMPTY",
    }


# ─── API ─────────────────────────────────────────────────────


def _patches(k8s):
    return (
        patch("app.api.model_deployments.k8s_for_cluster", AsyncMock(return_value=k8s)),
        patch("app.api.model_deployments.resolve_entries", AsyncMock(side_effect=_resolved)),
        _phases({"models-pvc": "Bound"}),
    )


async def test_create_stores_litellm_name_blank_means_default(client_for_user, super_user, mock_db):
    mock_db.execute = AsyncMock(return_value=_result(None))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    a, b, c = _patches(_k8s())
    with a, b, c:
        async with client_for_user(super_user) as client:
            named = await client.post("/api/model-deployments", json={**DEPLOY, "litellm_model_name": " meta/Llama-3 "})
            blank = await client.post("/api/model-deployments", json={**DEPLOY, "litellm_model_name": "  "})
    assert named.status_code == 201, named.text
    assert named.json()["litellm_model_name"] == "meta/Llama-3"
    assert blank.json()["litellm_model_name"] is None


async def test_rename_drops_the_old_litellm_entry(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(litellm_model_id="old-id", status="Ready")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_litellm.delete_model = AsyncMock(return_value={})
    a, b, c = _patches(_k8s())
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"litellm_model_name": "meta/Llama-3"})
    assert resp.status_code == 200, resp.text
    mock_litellm.delete_model.assert_awaited_once_with("old-id")
    data = resp.json()
    # the reconciler registers the new name on the Updating → Ready transition
    assert data["litellm_model_id"] is None and data["status"] == "Updating"


async def test_served_name_change_also_re_registers(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(litellm_model_id="old-id")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_litellm.delete_model = AsyncMock(return_value={})
    a, b, c = _patches(_k8s())
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(
                f"/api/model-deployments/{row.id}", json={"engine_args": {"served-model-name": "x"}}
            )
    assert resp.status_code == 200, resp.text
    mock_litellm.delete_model.assert_awaited_once_with("old-id")


async def test_update_that_keeps_the_registration_keeps_the_entry(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(litellm_model_id="old-id", litellm_model_name="meta/Llama-3")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_litellm.delete_model = AsyncMock(return_value={})
    a, b, c = _patches(_k8s())
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(
                f"/api/model-deployments/{row.id}", json={"replicas": 3, "litellm_model_name": "meta/Llama-3"}
            )
    assert resp.status_code == 200, resp.text
    mock_litellm.delete_model.assert_not_awaited()
    assert resp.json()["litellm_model_id"] == "old-id"


async def test_failed_apply_keeps_the_litellm_entry(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(litellm_model_id="old-id")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_litellm.delete_model = AsyncMock(return_value={})
    k8s = _k8s()
    k8s.create_or_patch = AsyncMock(side_effect=RuntimeError("apiserver down"))
    a, b, c = _patches(k8s)
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"litellm_model_name": "new"})
    assert resp.status_code == 502
    mock_litellm.delete_model.assert_not_awaited()


async def test_reregister_requires_ready(client_for_user, super_user, mock_db, mock_litellm):
    row = _dep_row(status="Pending")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_litellm.create_model = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.post(f"/api/model-deployments/{row.id}/litellm-registration")
    assert resp.status_code == 409
    mock_litellm.create_model.assert_not_awaited()


async def test_reregister_creates_new_then_drops_old(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(status="Ready", litellm_model_id="old-id", litellm_model_name="meta/Llama-3")
    mock_db.execute = AsyncMock(side_effect=[_result(row), _result(None)])  # row, then catalog lookup
    order = []
    created = {"model_info": {"id": "new-id"}}
    mock_litellm.create_model = AsyncMock(side_effect=lambda **kw: order.append("create") or created)
    mock_litellm.delete_model = AsyncMock(side_effect=lambda mid: order.append(f"delete:{mid}") or {})
    async with client_for_user(super_user) as client:
        resp = await client.post(f"/api/model-deployments/{row.id}/litellm-registration")
    assert resp.status_code == 200, resp.text
    assert order == ["create", "delete:old-id"]
    assert resp.json()["litellm_model_id"] == "new-id"
    kwargs = mock_litellm.create_model.await_args.kwargs
    assert kwargs["model_name"] == "meta/Llama-3" and kwargs["litellm_model"] == "openai//models/llama"
    from app.db.models.custom_model_catalog import CustomModelCatalog

    assert [c.args[0].model_name for c in mock_db.add.call_args_list if isinstance(c.args[0], CustomModelCatalog)] == [
        "meta/Llama-3"
    ]


async def test_reregister_failure_keeps_the_old_entry(client_for_user, super_user, mock_db, mock_litellm):
    row = _agg(status="Ready", litellm_model_id="old-id")
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_litellm.create_model = AsyncMock(side_effect=RuntimeError("litellm down"))
    mock_litellm.delete_model = AsyncMock()
    async with client_for_user(super_user) as client:
        resp = await client.post(f"/api/model-deployments/{row.id}/litellm-registration")
    assert resp.status_code == 502
    mock_litellm.delete_model.assert_not_awaited()
    assert row.litellm_model_id == "old-id"


async def test_reregister_pd_waits_for_router(client_for_user, super_user, mock_db, mock_litellm):
    row = _dep_row(status="Ready", serving_mode="pd", pd_config={"prefill": {"replicas": 1}, "decode": {"replicas": 1}},
                   router_stack_id=uuid.uuid4())
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_litellm.create_model = AsyncMock()
    target = AsyncMock(return_value=(None, "router 'x' is OutOfSync/Progressing"))
    with patch("app.jobs.reconcile_deployments._router_target", target):
        async with client_for_user(super_user) as client:
            resp = await client.post(f"/api/model-deployments/{row.id}/litellm-registration")
    assert resp.status_code == 409 and "OutOfSync" in resp.text
    mock_litellm.create_model.assert_not_awaited()


def test_litellm_names_may_be_shared():
    """Two deployments, one LiteLLM name: nothing rejects it (LiteLLM load-balances them)."""
    from app.db.models.custom_model_deployment import CustomModelDeployment

    col = CustomModelDeployment.__table__.c.litellm_model_name
    assert not col.unique and col.nullable


async def test_unrenderable_previous_state_leaves_litellm_alone(client_for_user, super_user, mock_db, mock_litellm):
    """A row whose old state can't be rendered gives no baseline: keep the entry rather than guess."""
    row = _dep_row(litellm_model_id="old-id")  # storage snapshot without a type: does not render
    mock_db.execute = AsyncMock(return_value=_result(row))
    mock_db.refresh = AsyncMock(side_effect=_refresh)
    mock_litellm.delete_model = AsyncMock(return_value={})
    a, b, c = _patches(_k8s())
    with a, b, c:
        async with client_for_user(super_user) as client:
            resp = await client.put(f"/api/model-deployments/{row.id}", json={"replicas": 2})
    assert resp.status_code == 200, resp.text
    mock_litellm.delete_model.assert_not_awaited()


async def test_detail_shows_what_litellm_sends(client_for_user, super_user, mock_db):
    row = _agg(litellm_model_name="meta/Llama-3")
    stacks = AsyncMock()
    stacks.scalars = lambda: type("S", (), {"all": lambda self: []})()
    mock_db.execute = AsyncMock(side_effect=[_result(row), stacks, _result(None)])
    with patch("app.api.model_deployments._recipe_names", AsyncMock(return_value={})):
        async with client_for_user(super_user) as client:
            resp = await client.get(f"/api/model-deployments/{row.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["litellm_model_name"] == "meta/Llama-3"
    assert data["litellm_model"] == "openai//models/llama"
