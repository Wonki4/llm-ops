"""Portal MCP read-only tools: same data as the REST routes, super users only."""

import types
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.api import gpu_profiles, k8s_clusters, model_deployments, serving_overview, serving_recipes, storages
from app.db.models.custom_user import GlobalRole
from app.mcp import tools

PHASE1_TOOLS = {
    "whoami", "list_clusters", "list_gpu_types", "list_gpu_profiles", "list_storages", "get_storage",
    "list_recipes", "get_recipe", "list_deployments", "get_deployment", "get_deployment_events",
    "serving_overview",
}

ID = str(uuid.uuid4())

# tool call -> (route module, route function name, kwargs the route must receive besides user/db)
WIRING = [
    (lambda: tools.list_clusters(), k8s_clusters, "list_clusters", {}),
    (lambda: tools.list_gpu_types(), gpu_profiles, "list_gpu_types", {}),
    (lambda: tools.list_gpu_profiles(ID), gpu_profiles, "list_gpu_profiles", {"cluster_id": ID}),
    (lambda: tools.list_storages(), storages, "list_storages", {"kind": None, "q": None}),
    (lambda: tools.get_storage(ID), storages, "get_storage", {"storage_id": ID}),
    (lambda: tools.list_recipes(), serving_recipes, "list_recipes", {}),
    (lambda: tools.get_recipe(ID), serving_recipes, "get_recipe", {"recipe_id": ID}),
    (lambda: tools.list_deployments(), model_deployments, "list_deployments", {}),
    (lambda: tools.get_deployment(ID), model_deployments, "get_deployment", {"deployment_id": ID}),
    (
        lambda: tools.get_deployment_events(ID, limit=7),
        model_deployments,
        "list_deployment_events",
        {"deployment_id": ID, "limit": 7},
    ),
    (lambda: tools.serving_overview(), serving_overview, "serving_overview", {}),
]
ALL_CALLS = [w[0] for w in WIRING] + [
    lambda: tools.whoami(), lambda: tools.preview_recipe(None), lambda: tools.create_recipe(None),
    lambda: tools.deploy_from_recipe(ID, None), lambda: tools.preview_deployment_from_recipe(ID, None),
    lambda: tools.preview_deployment_recipe_patch(ID, {}), lambda: tools.apply_deployment_recipe_patch(ID, {}, "test"),
    lambda: tools.get_rollout_status(ID), lambda: tools.get_deployment_diagnostics(ID, "decode"),
    lambda: tools.smoke_test_deployment(ID, {}), lambda: tools.retry_aggregated_router(ID),
]


def _result(scalar=None, all_rows=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar
    r.scalars.return_value.all.return_value = all_rows or []
    return r


def _recipe(**kw):
    base = dict(
        id=uuid.uuid4(), name="r1", description=None, model_path="/w/llama", image="vllm:latest",
        gpu_count=1, gpu_resource_key="nvidia.com/gpu", cpu_request=None, cpu_limit=None,
        memory_request=None, memory_limit=None, node_selector=None, tolerations=None,
        storages=[], vllm_extra_args=["--tensor-parallel-size", "2"],
        env={"HF_HUB_OFFLINE": "1"}, engine="vllm", engine_args=None,
        created_by=None, updated_by=None, created_at=None, updated_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def as_caller(monkeypatch, mock_db):
    """Tools open `mock_db` as their session and see `user` as the verified caller."""

    @asynccontextmanager
    async def session():
        yield mock_db

    monkeypatch.setattr(tools, "async_session_factory", session)
    mock_db.commit = AsyncMock()

    def _as(user=None, *, error: ToolError | None = None) -> AsyncMock:
        gate = AsyncMock(return_value=user, side_effect=error)
        monkeypatch.setattr(tools, "require_mcp_super_user", gate)
        return gate

    return _as


async def test_registers_serving_tools_with_correct_write_annotations():
    server = MCPServer("t")
    tools.register_tools(server)

    listed = await server.list_tools()

    reads = PHASE1_TOOLS | {"preview_recipe", "preview_deployment_from_recipe", "preview_deployment_recipe_patch", "get_rollout_status", "get_deployment_diagnostics"}
    writes = {"create_recipe", "deploy_from_recipe", "apply_deployment_recipe_patch", "retry_aggregated_router", "smoke_test_deployment"}
    assert {t.name for t in listed if t.annotations.read_only_hint} == reads
    assert {t.name for t in listed if not t.annotations.read_only_hint} == writes


async def test_list_recipes_equals_rest_route_output(as_caller, mock_db, super_user):
    as_caller(super_user)
    mock_db.execute = AsyncMock(return_value=_result(all_rows=[_recipe(), _recipe(name="r2")]))

    tool_out = await tools.list_recipes()
    rest_out = await serving_recipes.list_recipes(user=super_user, db=mock_db)

    assert tool_out == rest_out
    assert tool_out["recipes"][0]["env"] == {"HF_HUB_OFFLINE": "1"}  # shown as the UI shows it
    mock_db.commit.assert_awaited()


@pytest.mark.parametrize("call, module, name, kwargs", WIRING, ids=[w[2] for w in WIRING])
async def test_each_tool_returns_its_rest_route_output(
    as_caller, mock_db, super_user, monkeypatch, call, module, name, kwargs
):
    as_caller(super_user)
    route = AsyncMock(return_value={"from": name})
    monkeypatch.setattr(module, name, route)

    assert await call() == {"from": name}
    route.assert_awaited_once_with(**kwargs, user=super_user, db=mock_db)


async def test_get_recipe_missing_becomes_tool_error(as_caller, mock_db, super_user):
    as_caller(super_user)
    mock_db.execute = AsyncMock(return_value=_result(scalar=None))

    with pytest.raises(ToolError, match="404: Recipe not found"):
        await tools.get_recipe(ID)
    mock_db.commit.assert_not_awaited()


async def test_malformed_id_becomes_tool_error(as_caller, super_user):
    as_caller(super_user)

    with pytest.raises(ToolError, match="invalid argument"):
        await tools.get_recipe("my-recipe-name")


@pytest.mark.parametrize("call", ALL_CALLS)
async def test_non_super_user_gets_no_data(as_caller, mock_db, call):
    as_caller(error=ToolError("super user only"))
    mock_db.execute = AsyncMock()

    with pytest.raises(ToolError) as e:
        await call()

    assert str(e.value) == "super user only"
    mock_db.execute.assert_not_awaited()
    mock_db.commit.assert_not_awaited()


async def test_whoami_returns_identity(as_caller, super_user):
    as_caller(super_user)

    assert await tools.whoami() == {"user_id": "admin001", "global_role": GlobalRole.SUPER_USER.value}
