"""Portal MCP tools for the serving catalog and deployment recovery.

Each tool calls the REST route function that serves the same object, so the
agent sees exactly what the portal UI sees (same queries, same serialization).
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import HTTPException
from kubernetes_asyncio.client.exceptions import ApiException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.api import gpu_profiles, k8s_clusters, model_deployments, serving_recipes, storages
from app.api import serving_overview as serving_overview_api
from app.clients.k8s import K8sNotConfigured
from app.db.models.custom_user import CustomUser
from app.db.session import async_session_factory
from app.mcp import serving
from app.mcp.auth import require_mcp_super_user

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


async def _run(route_fn: Callable[..., Awaitable[dict]], **kwargs: Any) -> dict:
    """Run a REST route function as the verified caller, in its own portal DB session."""
    async with async_session_factory() as db:
        user = await require_mcp_super_user(db)
        try:
            result = await route_fn(**kwargs, user=user, db=db)
        except HTTPException as e:
            raise ToolError(f"{e.status_code}: {e.detail}") from e
        except ApiException as e:
            raise ToolError(f"Kubernetes request failed: HTTP {e.status} {e.reason}") from e
        except K8sNotConfigured as e:
            raise ToolError(str(e)) from e
        except ValueError as e:  # e.g. an id that is not a UUID
            raise ToolError(f"invalid argument: {e}") from e
        await db.commit()
        return result


async def _identity(*, user: CustomUser, db: Any) -> dict:
    return {"user_id": user.user_id, "global_role": user.global_role.value}


async def whoami() -> dict:
    return await _run(_identity)


async def list_clusters() -> dict:
    return await _run(k8s_clusters.list_clusters)


async def list_gpu_types() -> dict:
    return await _run(gpu_profiles.list_gpu_types)


async def list_gpu_profiles(cluster_id: str) -> dict:
    return await _run(gpu_profiles.list_gpu_profiles, cluster_id=cluster_id)


async def list_storages() -> dict:
    return await _run(storages.list_storages, kind=None, q=None)


async def get_storage(storage_id: str) -> dict:
    return await _run(storages.get_storage, storage_id=storage_id)


async def list_recipes() -> dict:
    return await _run(serving_recipes.list_recipes)


async def get_recipe(recipe_id: str) -> dict:
    return await _run(serving_recipes.get_recipe, recipe_id=recipe_id)


async def list_deployments() -> dict:
    return await _run(model_deployments.list_deployments)


async def get_deployment(deployment_id: str) -> dict:
    return await _run(model_deployments.get_deployment, deployment_id=deployment_id)


async def get_deployment_events(deployment_id: str, limit: int = 100) -> dict:
    return await _run(model_deployments.list_deployment_events, deployment_id=deployment_id, limit=limit)


async def serving_overview() -> dict:
    return await _run(serving_overview_api.serving_overview)


_DESCRIPTIONS: list[tuple[Callable[..., Awaitable[dict]], str]] = [
    (whoami, "The portal user this MCP session acts as (user id and global role)."),
    (
        list_clusters,
        "Kubernetes clusters the portal deploys to (namespace, API server, default flag; never the "
        "kubeconfig). Each item's `id` is the `cluster_id` `list_gpu_profiles` takes.",
    ),
    (
        list_gpu_types,
        "GPU types (enabled GPU profile names) and, per type, the clusters that offer it with the "
        "resource key, VRAM and node label value. This is the GPU picker of the recipe form.",
    ),
    (
        list_gpu_profiles,
        "All GPU profiles of one cluster: node label key/value, GPU resource key, tolerations, VRAM, "
        "enabled. `cluster_id` comes from `list_clusters`.",
    ),
    (
        list_storages,
        "Named model storages recipes mount, with the clusters/namespaces each is bound on and how many "
        "recipes and deployments use it. Each item's `id` is the `storage_id` `get_storage` takes.",
    ),
    (
        get_storage,
        "One storage with its per-cluster bindings (PVC claim or NFS server/path, sub path) and the "
        "recipes and deployments using it. `storage_id` comes from `list_storages`.",
    ),
    (
        list_recipes,
        "Serving recipes, newest first: model repository (hf_repo_id), runtime model path, image, engine and its args, GPU "
        "count and resources, env, storages. Each item's `id` is the `recipe_id` `get_recipe` takes.",
    ),
    (get_recipe, "One serving recipe in full. `recipe_id` comes from `list_recipes`."),
    (
        list_deployments,
        "Model deployments, newest first, with their status. Each item's `id` is the `deployment_id` "
        "`get_deployment` and `get_deployment_events` take.",
    ),
    (
        get_deployment,
        "One model deployment in full, plus the LiteLLM model it is registered as, linked llm-d stacks "
        "with live status, and stale storages. `deployment_id` comes from `list_deployments`.",
    ),
    (
        get_deployment_events,
        "Status events the portal recorded for one deployment (status transitions, severity, message), "
        "newest first, at most `limit` (max 500). `deployment_id` comes from `list_deployments`.",
    ),
    (
        serving_overview,
        "One row per self-served model showing where it sits in the recipe → deployment → benchmark → "
        "catalog pipeline, with llm-d stacks and external servings.",
    ),
]


async def preview_recipe(body: serving_recipes.PreviewRecipeBody) -> dict:
    """Validate a draft recipe and render its YAML and warnings without applying it."""
    return await _run(serving_recipes.preview_recipe, body=body)


async def create_recipe(body: serving_recipes.RecipeBody) -> dict:
    """Save a serving recipe, including independent prefill and decode settings for PD."""
    return await _run(serving.create_recipe, body=body)


async def preview_deployment_from_recipe(recipe_id: str, target: serving.DeployTarget) -> dict:
    """Preview the target deployment's LiteLLM registration and aggregated ingress before applying the recipe."""
    return await _run(serving.preview_deployment, recipe_id=recipe_id, target=target)


async def deploy_from_recipe(recipe_id: str, target: serving.DeployTarget) -> dict:
    """Apply the saved recipe. Keep the deployment id and rollout map for subsequent checks."""
    return await _run(serving.deploy, recipe_id=recipe_id, target=target)


async def preview_deployment_recipe_patch(deployment_id: str, patch: dict) -> dict:
    """Preview a JSON Merge Patch to this deployment's linked shared recipe.
    Return shared recipe changes, this deployment's manifest diff and a target-bound preview_id.
    """
    return await _run(serving.recovery_recipe, deployment_id=deployment_id, patch=patch)


async def apply_deployment_recipe_patch(deployment_id: str, patch: dict, preview_id: str) -> dict:
    """Save the previewed shared recipe patch and redeploy only the specified deployment."""
    return await _run(serving.recovery_recipe, deployment_id=deployment_id, patch=patch, preview_id=preview_id)


async def retry_aggregated_router(deployment_id: str) -> dict:
    """Reapply this deployment's dedicated llm-d stack without rolling its model pods."""
    return await _run(model_deployments.retry_aggregated_router, deployment_id=deployment_id)


async def get_rollout_status(deployment_id: str, rollout: dict[str, serving.Revision] | None = None) -> dict:
    """Check live Deployment generations, pool readiness and linked router sync/health.
    Pass the rollout map returned by deploy/apply to detect superseded writes.
    """
    return await _run(serving.rollout_status, deployment_id=deployment_id, rollout=rollout)


async def get_deployment_diagnostics(
    deployment_id: str, role: serving.Role, tail_lines: Annotated[int, Field(ge=1, le=100)] = 40,
) -> dict:
    """Inspect events, pod conditions and bounded current/previous logs for one pool or linked router."""
    return await _run(serving.diagnostics, deployment_id=deployment_id, role=role, tail_lines=tail_lines)


async def smoke_test_deployment(deployment_id: str, rollout: dict[str, serving.Revision]) -> dict:
    """Run a short chat inference through the exact LiteLLM deployment after the given rollout is ready."""
    return await _run(serving.smoke, deployment_id=deployment_id, rollout=rollout)


def register_tools(server: MCPServer) -> None:
    for fn, description in _DESCRIPTIONS:
        server.tool(description=description, annotations=READ_ONLY)(fn)
    for fn in (preview_recipe, preview_deployment_from_recipe, preview_deployment_recipe_patch,
               get_rollout_status, get_deployment_diagnostics):
        server.tool(annotations=READ_ONLY)(fn)
    for fn in (create_recipe, deploy_from_recipe, apply_deployment_recipe_patch, retry_aggregated_router, smoke_test_deployment):
        server.tool(annotations=ToolAnnotations(read_only_hint=False))(fn)
