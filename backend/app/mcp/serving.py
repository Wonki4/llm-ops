"""Serving recovery operations invoked inside the authenticated MCP database session."""

import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from kubernetes_asyncio.client.exceptions import ApiException
from pydantic import BaseModel

from app.api import model_deployments as deployments
from app.api import serving_recipes as recipes
from app.api.recipe_deployment import DeployTarget, deploy_body
from app.clients.litellm import get_litellm_client
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.services import aggregated_router, llmd_stacks, pd_serving, recipe_reapply
from app.services.clusters import k8s_for_cluster
from app.services.deployment_status import classify
from app.services.llmd_manifests import llmd_service_name
from app.services.model_deployment_manifests import k8s_resource_names
from app.services.serving_diagnostics import workload_diagnostics

Role = Literal["aggregated", "prefill", "decode", "router"]


async def create_recipe(body, user, db):
    return await recipes.create_recipe(recipes.CreateRecipeBody(**body.model_dump()), user, db)


async def preview_deployment(recipe_id, target: DeployTarget, user, db):
    recipe = await recipes.get_recipe(recipe_id, user, db)
    body = deploy_body(recipe, target)
    return await deployments.preview_deployment_registration(body, include_ingress=True, user=user, db=db)


async def deploy(recipe_id, target: DeployTarget, user, db):
    recipe = await recipes.get_recipe(recipe_id, user, db)
    return await deployments.create_deployment(deploy_body(recipe, target), user, db)


def merge_patch(current, patch):
    """JSON Merge Patch: nested objects merge, null removes a key, arrays replace."""
    if not isinstance(patch, dict):
        return patch
    result = dict(current) if isinstance(current, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


async def recovery_recipe(deployment_id, patch, user, db, *, preview_id=None):
    """Patch the linked shared recipe and roll only the deployment named in the preview."""
    applying = preview_id is not None
    dep = await deployments._deployment_or_404(db, deployment_id, for_update=applying)
    if not dep.recipe_id:
        raise HTTPException(409, "Deployment has no linked recipe")
    recipe = await db.get(CustomServingRecipe, dep.recipe_id, with_for_update=applying)
    if recipe is None or (recipe.serving_mode or "aggregated") != (dep.serving_mode or "aggregated"):
        raise HTTPException(409, "Linked recipe is missing or its serving mode differs")

    unknown = patch.keys() - set(recipe_reapply.RECIPE_FIELDS)
    if unknown:
        raise ValueError(f"Fields outside the deployment recipe: {sorted(unknown)}")
    source = recipes._serialize(recipe)
    fields = {k: source[k] for k in recipes.RecipeBody.model_fields}
    merged = merge_patch(fields, patch)
    body = recipes.RecipeBody.model_validate(merged)
    old_pd, new_pd = fields["pd_config"] or {}, merged.get("pd_config") or {}
    if new_pd.get("router") != old_pd.get("router"):
        raise ValueError("Router stack settings cannot be changed through deployment recovery")
    for role in pd_serving.ROLES:
        if (new_pd.get(role) or {}).get("replicas", 1) != (old_pd.get(role) or {}).get("replicas", 1):
            raise ValueError("Pool replica counts cannot be changed through deployment recovery")

    # Validate the full recipe, but persist only fields explicitly patched.
    validated = body.model_dump()
    updates = {k: validated[k] for k in patch if merged.get(k) != fields[k] and validated[k] != fields[k]}
    proposed = CustomServingRecipe(id=recipe.id, **(fields | updates))
    diff = await deployments.deployment_recipe_diff(db, dep, proposed)
    identity = [str(dep.id), str(dep.cluster_id), dep.namespace, source, patch, diff]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not applying:
        return {
            **diff,
            "deployment_id": str(dep.id),
            "preview_id": digest,
            "recipe_changes": {k: {"current": fields[k], "proposed": v} for k, v in updates.items()},
        }
    if preview_id != digest:
        raise HTTPException(409, "Target, shared recipe or deployment settings changed; preview the patch again")
    if diff["rollout"]["blocked"]:
        raise HTTPException(409, diff["rollout"]["blocked"])
    if not any(m["changed"] for m in diff["manifests"]):
        raise HTTPException(409, "No changes to the deployment manifests")
    if updates:
        await recipes._check_known_storages(db, body)
        for key, value in updates.items():
            setattr(recipe, key, value)
        recipe.updated_by = user.user_id
    return await deployments.apply_deployment_recipe(db, dep, recipe, get_litellm_client(), user)


class Revision(BaseModel):
    uid: str
    generation: int


async def rollout_status(deployment_id, rollout, user, db):
    dep = await deployments._deployment_or_404(db, deployment_id)
    k8s = await k8s_for_cluster(db, dep.cluster_id)
    names = k8s_resource_names(dep)
    pools = {
        k.removesuffix("_deployment") if k != "deployment" else "aggregated": v
        for k, v in names.items()
        if k.endswith("deployment")
    }
    if rollout is not None and set(rollout) != set(pools.values()):
        raise ValueError("rollout must identify every Deployment returned by deploy or reapply")
    results = {}
    for role, name in pools.items():
        try:
            live = await k8s.read_deployment_status(dep.namespace, name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            results[role] = {"name": name, "status": "Missing"}
            continue
        expected = rollout[name] if rollout else None
        generation = live["generation"]
        if expected and (expected.uid != live["uid"] or expected.generation != generation):
            state, message = "Superseded", "This Deployment was replaced or changed after apply"
        elif (live["observed_generation"] or 0) < generation:
            state, message = "Updating", "Controller has not observed this generation"
        else:
            state, message = classify(live, live["desired"])
        results[role] = {**live, "name": name, "status": state, "message": message}
    ready = all(pool["status"] == "Ready" for pool in results.values())
    router = None
    if aggregated_router.routed(dep):
        router = await deployments._router_stack_summary(db, dep)
        ready = bool(
            ready and not getattr(dep, "router_apply_error", None) and router
            and router["sync_status"] == "Synced" and router["health_status"] == "Healthy"
        )
        if getattr(dep, "router_apply_error", None):
            router = {**(router or {}), "status_message": dep.router_apply_error}
    return {"deployment_id": deployment_id, "ready": ready, "pools": results, "router": router}


async def diagnostics(deployment_id, role, tail_lines, user, db):
    dep = await deployments._deployment_or_404(db, deployment_id)
    cluster_id, ns = dep.cluster_id, dep.namespace
    router = role == "router"
    if aggregated_router.routed(dep) and router:
        stack = await db.get(CustomLlmdStack, dep.router_stack_id) if dep.router_stack_id else None
        if not stack:
            return {"role": role, "error": dep.status_message or "No linked router"}
        cluster_id, ns, name = stack.cluster_id, stack.namespace, llmd_service_name(stack)
    else:
        key = "deployment" if role == "aggregated" else f"{role}_deployment"
        name = k8s_resource_names(dep).get(key)
        if not name:
            raise ValueError("Use prefill, decode or router for PD; aggregated for a single pool")
    k8s = await k8s_for_cluster(db, cluster_id)
    result = {"role": role}
    if router:
        result["application"] = await llmd_stacks.live_status(db, stack)
        if getattr(dep, "router_apply_error", None):
            result["router_apply_error"] = dep.router_apply_error
    try:
        result.update(await workload_diagnostics(k8s, ns, name, router=router, tail_lines=tail_lines))
    except ApiException as exc:
        if exc.status != 404:
            raise
        result.update({"name": name, "namespace": ns, "status": "Missing"})
    return result


async def smoke(deployment_id, rollout, user, db):
    live = await rollout_status(deployment_id, rollout, user, db)
    if not live["ready"]:
        return {"ok": False, "reason": "Rollout is not ready", "rollout_status": live}
    dep = await deployments._deployment_or_404(db, deployment_id)
    if not dep.litellm_model_id:
        return {"ok": False, "reason": "Waiting for LiteLLM registration; check deployment events"}
    result = await get_litellm_client().smoke_chat(dep.litellm_model_id)
    if result["ok"] and not (await rollout_status(deployment_id, rollout, user, db))["ready"]:
        return {"ok": False, "reason": "Rollout changed during the request"}
    return result
