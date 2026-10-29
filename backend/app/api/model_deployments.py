"""Admin endpoints for managing K8s-backed LLM deployments.

PR-A scope: create, list, get, delete. Status only reflects whatever the
worker has last synced — the worker is added in PR-B.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from kubernetes_asyncio.client.exceptions import ApiException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured, get_k8s_client  # noqa: F401
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.jobs import reconcile_deployments
from app.services import llmd_stacks, pd_router, pd_serving, recipe_reapply, serving_rollout
from app.services.clusters import default_namespace, k8s_for_cluster, scan_scopes, validate_namespace
from app.services.external_servings import scan_clusters
from app.services.gpu_profile_store import UnknownGpuTypeError, cluster_label_key, list_profiles, resolve_gpu_type
from app.services.gpu_profiles import apply_profile, match_profile, resolve_placement, validate_profile_name
from app.services.litellm_registration import litellm_name, registration
from app.services.llmd_links import portal_server, stacks_for_server
from app.services.llmd_manifests import stack_selector
from app.services.model_deployment_manifests import build_all, k8s_resource_names, live_owner
from app.services.recipe_import import attach_storages, build_recipe_draft
from app.services.serving_engines import (
    DEFAULT_IMAGES,
    ServingEngine,
    default_image,
    validate_engine_args,
    validate_port_flag,
)
from app.services.serving_probes import validate_probes
from app.services.serving_rollout import check_rollout
from app.services.storage_fields import pvc_access_warnings, recipe_warnings, reject_legacy_pvc
from app.services.storage_mounts import all_entries, validate_entries
from app.services.storage_store import (
    match_claims,
    pvc_index,
    resolve_entries,
    resolve_pool_storages,
    stale_storage_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model-deployments", tags=["model-deployments"])


DEFAULT_VLLM_IMAGE = DEFAULT_IMAGES["vllm"]  # kept for existing references


def _litellm_name(v: str | None) -> str | None:
    """Blank = use model_name. Any text LiteLLM accepts (slashes, capitals) is fine."""
    v = (v or "").strip()
    if len(v) > 256:
        raise ValueError("litellm_model_name must be at most 256 characters")
    return v or None


class CreateDeploymentRequest(BaseModel):
    model_name: str
    litellm_model_name: str | None = None  # name in LiteLLM; None = model_name; may be shared
    cluster_id: str | None = None  # registered K8s cluster; None = portal default
    namespace: str | None = None  # None/blank = the cluster's default namespace
    image: str | None = None  # None → default_image(engine)
    replicas: int = Field(1, ge=0)
    gpu_count: int = Field(1, ge=0)
    gpu_resource_key: str = "nvidia.com/gpu"
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    storages: list[dict] = Field(default_factory=list)
    model_path: str
    vllm_extra_args: list[str] | None = None
    env: dict | None = None
    engine: ServingEngine = "vllm"
    engine_args: dict[str, str | int | float | bool] | None = None
    probes: dict | None = None
    gpu_type: str | None = None
    ingress_host: str  # P/D: the router's host (the pools get no Ingress of their own)
    ingress_path: str = "/"
    ingress_class: str = "nginx"
    recipe_id: str | None = None  # recipe this deployment is launched from (informational)
    serving_mode: pd_serving.ServingMode = "aggregated"
    pd_config: dict | None = None
    runtime: dict | None = None
    router_stack_id: str | None = None  # P/D: link this llm-d stack instead of auto-creating one

    @field_validator("vllm_extra_args")
    @classmethod
    def _check_port_flag(cls, v: list[str] | None) -> list[str] | None:
        return validate_port_flag(v)

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)

    @field_validator("probes")
    @classmethod
    def _check_probes(cls, v: dict | None) -> dict | None:
        return validate_probes(v)

    @field_validator("gpu_type")
    @classmethod
    def _check_gpu_type(cls, v: str | None) -> str | None:
        return validate_profile_name(v) if v and v.strip() else None

    @field_validator("runtime")
    @classmethod
    def _check_runtime(cls, v: dict | None) -> dict | None:
        return pd_serving.validate_runtime(v)

    @model_validator(mode="before")
    @classmethod
    def _no_legacy_pvc(cls, data):
        return reject_legacy_pvc(data)

    @field_validator("litellm_model_name")
    @classmethod
    def _check_litellm_name(cls, v: str | None) -> str | None:
        return _litellm_name(v)

    @field_validator("namespace")
    @classmethod
    def _check_namespace(cls, v: str | None) -> str | None:
        return validate_namespace(v)

    @field_validator("storages")
    @classmethod
    def _check_storages(cls, v):
        return validate_entries(v)

    @model_validator(mode="after")
    def _check_pd(self):
        if self.serving_mode == "pd":
            self.pd_config = pd_serving.validate_pd_config(
                self.pd_config, engine=self.engine, base_extra_args=self.vllm_extra_args
            )
        else:
            self.pd_config = None
            self.router_stack_id = None
        return self


class UpdateDeploymentRequest(BaseModel):
    litellm_model_name: str | None = None  # null/blank = back to model_name
    image: str | None = None
    replicas: int | None = Field(None, ge=0)
    gpu_count: int | None = Field(None, ge=0)
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    storages: list[dict] | None = None
    model_path: str | None = None
    vllm_extra_args: list[str] | None = None
    env: dict | None = None
    engine: ServingEngine | None = None
    engine_args: dict[str, str | int | float | bool] | None = None
    probes: dict | None = None
    gpu_type: str | None = None
    ingress_host: str | None = None
    ingress_path: str | None = None
    ingress_class: str | None = None
    pd_config: dict | None = None  # validated against the row's engine/extra args in the handler
    runtime: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_legacy_pvc(cls, data):
        return reject_legacy_pvc(data)

    @field_validator("litellm_model_name")
    @classmethod
    def _check_litellm_name(cls, v: str | None) -> str | None:
        return _litellm_name(v)

    @field_validator("storages")
    @classmethod
    def _check_storages(cls, v):
        return None if v is None else validate_entries(v)

    @field_validator("vllm_extra_args")
    @classmethod
    def _check_port_flag(cls, v: list[str] | None) -> list[str] | None:
        return validate_port_flag(v)

    @field_validator("engine_args")
    @classmethod
    def _check_engine_args(cls, v: dict | None) -> dict | None:
        return validate_engine_args(v)

    @field_validator("probes")
    @classmethod
    def _check_probes(cls, v: dict | None) -> dict | None:
        return validate_probes(v)

    @field_validator("gpu_type")
    @classmethod
    def _check_gpu_type(cls, v: str | None) -> str | None:
        return validate_profile_name(v) if v and v.strip() else None

    @field_validator("runtime")
    @classmethod
    def _check_runtime(cls, v: dict | None) -> dict | None:
        return pd_serving.validate_runtime(v)


class RegisterExternalServingRequest(BaseModel):
    cluster_id: str | None = None
    namespace: str
    deployment_name: str
    model_name: str
    served_model_name: str
    api_base: str
    api_key: str | None = None


def _serialize(d: CustomModelDeployment, recipe_names: dict[uuid.UUID, str] | None = None) -> dict:
    recipe_id = getattr(d, "recipe_id", None)
    return {
        "id": str(d.id),
        "model_name": d.model_name,
        "litellm_model_name": getattr(d, "litellm_model_name", None),
        "cluster_id": str(d.cluster_id) if d.cluster_id else None,
        "recipe_id": str(recipe_id) if recipe_id else None,
        "recipe_name": (recipe_names or {}).get(recipe_id) if recipe_id else None,
        "namespace": d.namespace,
        "image": d.image,
        "replicas": d.replicas,
        "gpu_count": d.gpu_count,
        "gpu_resource_key": d.gpu_resource_key,
        "cpu_request": d.cpu_request,
        "cpu_limit": d.cpu_limit,
        "memory_request": d.memory_request,
        "memory_limit": d.memory_limit,
        "node_selector": d.node_selector,
        "tolerations": d.tolerations,
        "storages": list(d.storages or []),
        "model_path": d.model_path,
        "vllm_extra_args": d.vllm_extra_args,
        "env": d.env,
        "engine": d.engine or "vllm",
        "engine_args": d.engine_args,
        "probes": getattr(d, "probes", None),
        "gpu_type": getattr(d, "gpu_type", None),
        "serving_mode": getattr(d, "serving_mode", None) or "aggregated",
        "pd_config": getattr(d, "pd_config", None),
        "runtime": getattr(d, "runtime", None),
        "pd_status": getattr(d, "pd_status", None),
        "pd_summary": pd_serving.pd_status_summary(getattr(d, "pd_status", None)),
        "router_stack_id": str(d.router_stack_id) if getattr(d, "router_stack_id", None) else None,
        "router_stack_created": bool(getattr(d, "router_stack_created", False)),
        "ingress_host": d.ingress_host,
        "ingress_path": d.ingress_path,
        "ingress_class": d.ingress_class,
        "status": d.status,
        "status_message": d.status_message,
        "ready_replicas": d.ready_replicas,
        "service_cluster_ip": d.service_cluster_ip,
        "litellm_model_id": d.litellm_model_id,
        "last_synced_at": d.last_synced_at.isoformat() if d.last_synced_at else None,
        "created_by": d.created_by,
        "updated_by": d.updated_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


async def _recipe_names(db: AsyncSession, deployments: list[CustomModelDeployment]) -> dict[uuid.UUID, str]:
    """id → name for the recipes referenced by ``deployments`` (one query, or none)."""
    ids = {d.recipe_id for d in deployments if getattr(d, "recipe_id", None)}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(CustomServingRecipe.id, CustomServingRecipe.name).where(CustomServingRecipe.id.in_(ids))
        )
    ).all()
    return {rid: name for rid, name in rows}


@router.get("")
async def list_deployments(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).order_by(CustomModelDeployment.created_at.desc()))
    deployments = list(result.scalars().all())
    stacks = list((await db.execute(select(CustomLlmdStack))).scalars().all())
    names = await _recipe_names(db, deployments)
    out = []
    for d in deployments:
        linked = stacks_for_server(portal_server(d), stacks)
        out.append({**_serialize(d, names), "llmd_stack_count": len(linked)})
    return {"deployments": out}


def _serialize_registration(r: CustomExternalServing) -> dict:
    return {
        "id": str(r.id),
        "model_name": r.model_name,
        "api_base": r.api_base,
        "litellm_model_id": r.litellm_model_id,
    }


@router.get("/external")
async def list_external_servings(
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Live-scan for vLLM/SGLang deployments not managed by the portal.

    Looks in the representative cluster's namespace plus wherever a registration
    lives (so a registered serving never drops off the list), not cluster-wide.
    """
    regs = (await db.execute(select(CustomExternalServing))).scalars().all()
    scopes = await scan_scopes(db, [(r.cluster_id, r.namespace) for r in regs])
    servings, errors = await scan_clusters(scopes)

    reg_map = {(str(r.cluster_id) if r.cluster_id else None, r.namespace, r.deployment_name): r for r in regs}
    for s in servings:
        r = reg_map.get((s["cluster_id"], s["namespace"], s["deployment_name"]))
        s["registration"] = _serialize_registration(r) if r else None

    return {"servings": servings, "errors": errors}


@router.get("/external/recipe-draft")
async def external_recipe_draft(
    namespace: str,
    deployment_name: str,
    cluster_id: str | None = None,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reverse-engineer a recipe draft from a live (non-portal) Deployment.

    Reads the Deployment spec and parses its launch command, resources, env and
    volumes into recipe fields. Nothing is saved: the response is a pre-filled
    form plus warnings for every guess or dropped setting, and the operator
    reviews it before creating the recipe.
    """
    k8s = await k8s_for_cluster(db, cluster_id)
    try:
        spec = await k8s.read_deployment(namespace, deployment_name)
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("read_deployment failed for %s/%s", namespace, deployment_name)
        raise HTTPException(status_code=502, detail="Failed to read the Deployment from the cluster; check logs")
    if spec is None:
        raise HTTPException(status_code=404, detail="Deployment not found in the cluster")
    out = build_recipe_draft(spec)
    cid = uuid.UUID(cluster_id) if cluster_id else None
    claims = [m["claim_name"] for m in out["draft"].get("pvc_mounts") or []]
    attach_storages(out["draft"], out["warnings"], await match_claims(db, cid, namespace, claims))
    label_key = await cluster_label_key(db, cid)
    profiles = await list_profiles(db, cid, enabled_only=True)
    profile = match_profile(profiles, label_key, out["draft"].get("node_selector"))
    if profile is not None:
        selector = dict(out["draft"].get("node_selector") or {})
        selector.pop(profile.label_key or label_key, None)
        out["draft"]["node_selector"] = selector or None
        out["draft"]["gpu_type"] = profile.name
        out["draft"]["gpu_resource_key"] = profile.gpu_resource_key
    else:
        out["draft"]["gpu_type"] = None
    return out


@router.post("/external/register", status_code=status.HTTP_201_CREATED)
async def register_external_serving(
    body: RegisterExternalServingRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Register a discovered external serving with LiteLLM (/model/new)."""
    cid = uuid.UUID(body.cluster_id) if body.cluster_id else None
    existing = await db.execute(
        select(CustomExternalServing).where(
            CustomExternalServing.cluster_id == cid,
            CustomExternalServing.namespace == body.namespace,
            CustomExternalServing.deployment_name == body.deployment_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This serving is already registered")

    try:
        result = await litellm.create_model(
            model_name=body.model_name,
            litellm_model=f"openai/{body.served_model_name}",
            api_base=body.api_base,
            api_key=body.api_key or "EMPTY",
        )
    except Exception:
        logger.exception("LiteLLM /model/new failed for external serving %s", body.deployment_name)
        raise HTTPException(status_code=502, detail="LiteLLM registration failed; check logs")
    info = result.get("model_info") or {}
    model_id = info.get("id") or result.get("id")

    reg = CustomExternalServing(
        id=uuid.uuid4(),
        cluster_id=cid,
        namespace=body.namespace,
        deployment_name=body.deployment_name,
        model_name=body.model_name,
        api_base=body.api_base,
        litellm_model_id=str(model_id),
        registered_by=user.user_id,
    )
    db.add(reg)
    await db.flush()
    return {"registration": _serialize_registration(reg)}


@router.delete("/external/register/{registration_id}")
async def unregister_external_serving(
    registration_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Remove an external-serving registration (LiteLLM /model/delete + row).

    LiteLLM failures are swallowed (model may already be gone) so unregister
    stays idempotent; the mapping row is always removed.
    """
    result = await db.execute(
        select(CustomExternalServing).where(CustomExternalServing.id == uuid.UUID(registration_id))
    )
    reg = result.scalar_one_or_none()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    litellm_deleted = True
    try:
        await litellm.delete_model(reg.litellm_model_id)
    except Exception:
        logger.warning("LiteLLM /model/delete failed for %s; removing mapping anyway", reg.litellm_model_id)
        litellm_deleted = False

    await db.delete(reg)
    await db.flush()
    return {"deleted": True, "litellm_deleted": litellm_deleted}


@router.get("/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    stacks = list((await db.execute(select(CustomLlmdStack))).scalars().all())

    linked = []
    for st in stacks_for_server(portal_server(dep), stacks):
        linked.append(
            {
                "id": str(st.id),
                "name": st.name,
                "namespace": st.namespace,
                "selector": stack_selector(st.values_snapshot or {}),
                **(await llmd_stacks.live_status(db, st)),
            }
        )
    try:
        litellm_model = registration(dep)["litellm_model"]
    except Exception:
        litellm_model = None  # a row that no longer renders; the UI just omits the line
    return {
        **_serialize(dep, await _recipe_names(db, [dep])),
        "litellm_model": litellm_model,  # what LiteLLM sends the server (openai/<served name>)
        "llmd_stacks": linked,
        "router_stack": await _router_stack_summary(db, dep),
        "storage_stale": await stale_storage_ids(db, dep),
    }


async def _apply_pool_profiles(db: AsyncSession, dep: CustomModelDeployment, *, default_gpu_type: str | None) -> None:
    """Fold each pool's GPU profile (its own gpu_type, else the deploy-time one)
    into the pool's placement, the way ``apply_profile`` does for the row.
    Call it before ``apply_profile`` so a pool without its own placement starts
    from the recipe's, not from the row's already-profiled one."""
    cfg = dict(dep.pd_config or {})
    for role in pd_serving.ROLES:
        pool = dict(cfg.get(role) or {})
        gpu_type = pool.get("gpu_type") or default_gpu_type
        try:
            profile, label_key = await resolve_gpu_type(db, dep.cluster_id, gpu_type)
        except UnknownGpuTypeError as e:
            raise HTTPException(status_code=400, detail=f"{role}: {e}")
        placement = resolve_placement(
            profile,
            label_key,
            node_selector=pool.get("node_selector") if pool.get("node_selector") is not None else dep.node_selector,
            tolerations=pool.get("tolerations") if pool.get("tolerations") is not None else dep.tolerations,
            gpu_resource_key=pool.get("gpu_resource_key") or dep.gpu_resource_key,
        )
        pool["node_selector"] = placement.node_selector
        pool["tolerations"] = placement.tolerations
        pool["gpu_resource_key"] = placement.gpu_resource_key
        cfg[role] = pool
    dep.pd_config = cfg


async def _resolve_storages(
    db: AsyncSession, dep: CustomModelDeployment, entries: list, *, previous: list | None, previous_pd: dict | None
) -> None:
    """Snapshot the binding of every storage (base + P/D pools) for the row's cluster/namespace."""
    try:
        dep.storages = await resolve_entries(db, entries, dep.cluster_id, dep.namespace, previous=previous)
        if pd_serving.is_pd(dep):
            dep.pd_config = await resolve_pool_storages(
                db, dep.pd_config, dep.cluster_id, dep.namespace, previous=previous_pd
            )
    except ValueError as e:  # unresolved / unknown storage, reserved or duplicate volume name
        raise HTTPException(status_code=400, detail=str(e))


async def _deploy_warnings(db: AsyncSession, dep: CustomModelDeployment) -> list[str]:
    """model_path outside every storage, then the live PVC checks (``pvc_warnings``)."""
    return recipe_warnings(dep.model_path, dep.storages or [], dep.pd_config) + await pvc_warnings(db, dep)


async def pvc_warnings(db: AsyncSession, dep: CustomModelDeployment) -> list[str]:
    """PVCs that are missing / not Bound, or whose access modes don't fit how they are mounted.
    Bounded, best-effort lookup; placeholder claims of unbound storages (preview) are skipped."""
    resolved = [e.get("resolved") or {} for e in all_entries(dep)]
    claims = sorted(
        {r["claim_name"] for r in resolved if r.get("type") == "pvc" and not r["claim_name"].startswith("<")}
    )
    if not claims:
        return []
    pvcs = await pvc_index(db, dep.cluster_id, dep.namespace)
    if pvcs is None:  # cluster unreadable: no PVC warnings
        return []
    out = []
    for claim in claims:
        if claim not in pvcs:
            out.append(f"pvc_missing:{claim}")
        elif (pvcs[claim].get("phase") or "Pending") != "Bound":
            out.append(f"pvc_pending:{claim}")
    return out + pvc_access_warnings(dep, pvcs)


async def _router_stack_summary(db: AsyncSession, dep: CustomModelDeployment) -> dict | None:
    """The P/D serving's router stack (id/name/host + live ArgoCD state), None when unlinked."""
    stack_id = getattr(dep, "router_stack_id", None)
    if not stack_id:
        return None
    stack = await db.get(CustomLlmdStack, stack_id)
    if stack is None:
        return None
    return {
        "id": str(stack.id),
        "name": stack.name,
        "namespace": stack.namespace,
        "ingress_host": llmd_stacks.ingress_host(stack),
        "created_by_deployment": bool(getattr(dep, "router_stack_created", False)),
        **(await llmd_stacks.live_status(db, stack)),
    }


async def _attach_router(db: AsyncSession, dep: CustomModelDeployment, body: CreateDeploymentRequest, user_id) -> None:
    """Link the P/D serving to an llm-d router: the given stack, an existing
    ``<model>-router``, or a freshly created one (its failure is reported in
    status_message rather than failing the deploy — the pools are already up)."""
    if body.router_stack_id:
        stack = await db.get(CustomLlmdStack, uuid.UUID(body.router_stack_id))
        if stack is None:
            raise HTTPException(status_code=400, detail="router_stack_id does not match an llm-d stack")
        dep.router_stack_id = stack.id
        dep.router_stack_created = False
        return
    name = f"{dep.model_name}-router"
    existing = (await db.execute(select(CustomLlmdStack).where(CustomLlmdStack.name == name))).scalar_one_or_none()
    if existing is not None:
        dep.router_stack_id = existing.id
        dep.router_stack_created = False
        return
    router = (dep.pd_config or {}).get("router") or {}
    try:
        stack = await llmd_stacks.create_stack(
            db,
            name=name,
            target_model_name=dep.model_name,
            cluster_id=dep.cluster_id,
            namespace=dep.namespace,
            values=pd_router.router_values(dep),
            user_id=user_id,
            # Only the recipe's own pins: a blank one keeps following the portal default.
            chart_repo=router.get("chart_repo"),
            chart_name=router.get("chart_name"),
            chart_version=router.get("chart_version"),
            epp_registry=router.get("epp_registry"),
            epp_repository=router.get("epp_repository"),
            epp_tag=router.get("epp_tag"),
            ingress_host=dep.ingress_host,
            ingress_class=router.get("ingress_class") or dep.ingress_class,
        )
    except HTTPException as e:
        logger.warning("Router stack %s was not created for %s: %s", name, dep.model_name, e.detail)
        dep.status_message = f"Router stack '{name}' was not created: {e.detail}"
        return
    dep.router_stack_id = stack.id
    dep.router_stack_created = True


def _apply_error(e: Exception, dep: CustomModelDeployment) -> HTTPException:
    """The HTTP error for a failed K8s apply: the namespace cases get their own message."""
    if isinstance(e, ApiException):
        try:
            details = (json.loads(e.body or "{}") or {}).get("details") or {}
        except (TypeError, ValueError):
            details = {}
        if e.status == 404 and details.get("kind") == "namespaces":
            return HTTPException(
                status_code=400,
                detail=f"Namespace '{dep.namespace}' does not exist on this cluster; create it first",
            )
        if e.status == 403:
            return HTTPException(
                status_code=502,
                detail=f"The portal is not allowed to create resources in namespace '{dep.namespace}' (403 Forbidden); "
                "check its RBAC on that namespace",
            )
    logger.exception("K8s apply failed for %s", dep.model_name)
    return HTTPException(status_code=502, detail="Failed to apply K8s resources; check logs")


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: CreateDeploymentRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Uniqueness on model_name
    existing = await db.execute(
        select(CustomModelDeployment).where(CustomModelDeployment.model_name == body.model_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Deployment for '{body.model_name}' already exists")

    k8s = await k8s_for_cluster(db, body.cluster_id)
    cluster_uuid = uuid.UUID(body.cluster_id) if body.cluster_id else None
    namespace = body.namespace or await default_namespace(db, cluster_uuid)
    try:
        gpu_profile, gpu_label_key = await resolve_gpu_type(db, cluster_uuid, body.gpu_type)
    except UnknownGpuTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    dep = CustomModelDeployment(
        id=uuid.uuid4(),
        model_name=body.model_name,
        litellm_model_name=body.litellm_model_name,
        cluster_id=uuid.UUID(body.cluster_id) if body.cluster_id else None,
        namespace=namespace,
        image=body.image or default_image(body.engine),
        replicas=body.replicas,
        gpu_count=body.gpu_count,
        gpu_resource_key=body.gpu_resource_key,
        cpu_request=body.cpu_request,
        cpu_limit=body.cpu_limit,
        memory_request=body.memory_request,
        memory_limit=body.memory_limit,
        node_selector=body.node_selector,
        tolerations=body.tolerations,
        model_path=body.model_path,
        vllm_extra_args=body.vllm_extra_args,
        env=body.env,
        engine=body.engine,
        engine_args=body.engine_args,
        probes=body.probes,
        gpu_type=body.gpu_type,
        ingress_host=body.ingress_host,
        ingress_path=body.ingress_path,
        ingress_class=body.ingress_class,
        recipe_id=uuid.UUID(body.recipe_id) if body.recipe_id else None,
        serving_mode=body.serving_mode,
        pd_config=body.pd_config,
        runtime=body.runtime,
        status="Pending",
        created_by=user.user_id,
        updated_by=user.user_id,
    )
    await _resolve_storages(db, dep, body.storages, previous=None, previous_pd=None)
    if pd_serving.is_pd(dep):
        await _apply_pool_profiles(db, dep, default_gpu_type=body.gpu_type)
    apply_profile(dep, gpu_profile, gpu_label_key)
    try:
        check_rollout(dep)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.add(dep)
    await db.flush()
    await db.refresh(dep)

    # Apply to K8s (P/D: prefill + decode pools, no Ingress — the router is the entry)
    try:
        await k8s.create_or_patch(dep.namespace, build_all(dep, sidecar_image=settings.llmd_sidecar_image))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise _apply_error(e, dep)

    if pd_serving.is_pd(dep):
        await _attach_router(db, dep, body, user.user_id)

    return {**_serialize(dep), "warnings": await _deploy_warnings(db, dep)}


def _registration_key(dep: CustomModelDeployment) -> dict | None:
    """What /model/new would get, minus the P/D router URL (an update never changes it)."""
    try:
        return registration(dep, api_base="<router>" if pd_serving.is_pd(dep) else None)
    except Exception:
        return None


async def _unregister(litellm: LiteLLMClient, dep: CustomModelDeployment) -> None:
    """Drop the deployment's LiteLLM entry (best-effort) and forget its id."""
    try:
        await litellm.delete_model(dep.litellm_model_id)
    except Exception:
        logger.warning("LiteLLM /model/delete failed for %s (%s)", dep.model_name, dep.litellm_model_id)
    dep.litellm_model_id = None


@router.put("/{deployment_id}")
async def update_deployment(
    deployment_id: str,
    body: UpdateDeploymentRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Update mutable fields and reapply K8s manifests (rolls Deployment).

    Update covers both upgrade (image / args) and scale (replicas, gpu_count).
    When what LiteLLM is told changes (its name, the served name, the key or
    the URL), the old LiteLLM entry is removed after the apply succeeds and
    the reconciler registers the new one on the next Ready transition.
    """
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    k8s = await k8s_for_cluster(db, dep.cluster_id)
    return await _apply_update(db, k8s, litellm, dep, body.model_dump(exclude_unset=True), user)


async def _apply_update(
    db: AsyncSession,
    k8s: K8sClient,
    litellm: LiteLLMClient,
    dep: CustomModelDeployment,
    updates: dict,
    user: CustomUser,
) -> dict:
    """Set ``updates`` on the row, validate, reapply the manifests; shared by PUT and re-apply."""
    registered_as = _registration_key(dep)
    previous = list(dep.storages or [])
    previous_pd = dict(dep.pd_config or {}) if dep.pd_config else None
    for field, value in updates.items():
        setattr(dep, field, value)
    if pd_serving.is_pd(dep):
        try:
            dep.pd_config = pd_serving.validate_pd_config(
                dep.pd_config, engine=dep.engine, base_extra_args=dep.vllm_extra_args
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        dep.pd_config = None
    try:
        check_rollout(dep)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entries = updates["storages"] if updates.get("storages") is not None else previous
    await _resolve_storages(db, dep, entries, previous=previous, previous_pd=previous_pd)
    if pd_serving.is_pd(dep) and ("gpu_type" in updates or "pd_config" in updates):
        await _apply_pool_profiles(db, dep, default_gpu_type=dep.gpu_type)
    if updates.get("gpu_type"):
        try:
            gpu_profile, gpu_label_key = await resolve_gpu_type(db, dep.cluster_id, updates["gpu_type"])
        except UnknownGpuTypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        apply_profile(dep, gpu_profile, gpu_label_key)
    dep.updated_by = user.user_id
    dep.status = "Updating"
    dep.last_synced_at = datetime.utcnow()
    await db.flush()
    await db.refresh(dep)

    try:
        owner = await live_owner(k8s, dep)  # keep the selector the live Deployment was created with
        await k8s.create_or_patch(dep.namespace, build_all(dep, sidecar_image=settings.llmd_sidecar_image, owner=owner))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise _apply_error(e, dep)

    if dep.litellm_model_id and registered_as is not None and _registration_key(dep) != registered_as:
        await _unregister(litellm, dep)

    return {**_serialize(dep), "warnings": await _deploy_warnings(db, dep)}


@router.post("/{deployment_id}/litellm-registration")
async def reregister_deployment(
    deployment_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Register a Ready deployment in LiteLLM again, with what it should be told now.

    Fixes entries registered under a wrong name. The new entry is created
    first; the old one is removed only after that succeeded.
    """
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if dep.status != "Ready":
        raise HTTPException(
            status_code=409, detail=f"Deployment is {dep.status}; only a Ready deployment can be registered"
        )
    api_base = None
    if pd_serving.is_pd(dep):
        api_base, reason = await reconcile_deployments._router_target(db, dep)
        if api_base is None:
            raise HTTPException(status_code=409, detail=f"Router not ready: {reason}")

    model_id = await reconcile_deployments._register_with_litellm(litellm, dep, api_base=api_base)
    if not model_id:
        raise HTTPException(status_code=502, detail="LiteLLM /model/new failed; check logs")
    if dep.litellm_model_id:
        await _unregister(litellm, dep)
    dep.litellm_model_id = str(model_id)
    await reconcile_deployments._ensure_catalog(db, litellm_name(dep), user.user_id)
    await reconcile_deployments._record_event(
        db, dep, "LitellmRegistered", "info", None, None, f"Re-registered as LiteLLM model id={model_id}"
    )
    await db.flush()
    return _serialize(dep)


# ─── Re-apply a recipe ──────────────────────────────────────────


class ReapplyRequest(BaseModel):
    recipe_id: str | None = None  # None = the deployment's own recipe


async def _deployment_or_404(db: AsyncSession, deployment_id: str) -> CustomModelDeployment:
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return dep


async def _recipe_for(db: AsyncSession, dep: CustomModelDeployment, recipe_id: str | None) -> CustomServingRecipe:
    """The recipe to apply: the given one, else the deployment's; it must share the serving mode."""
    rid = recipe_id or (str(dep.recipe_id) if dep.recipe_id else None)
    if not rid:
        raise HTTPException(status_code=404, detail="Deployment is not linked to a recipe; pick one")
    try:
        key = uuid.UUID(rid)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe = await db.get(CustomServingRecipe, key)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_mode = getattr(recipe, "serving_mode", None) or "aggregated"
    dep_mode = dep.serving_mode or "aggregated"
    if recipe_mode != dep_mode:
        raise HTTPException(
            status_code=409,
            detail=f"Recipe serving mode '{recipe_mode}' differs from the deployment's '{dep_mode}'; redeploy instead",
        )
    return recipe


async def _proposed(db: AsyncSession, dep: CustomModelDeployment, recipe: CustomServingRecipe) -> tuple[dict, Any]:
    """``(values, proposed_row)``: the recipe's values with the deployment's GPU profile
    folded into the placement (base and P/D pools) exactly as ``_apply_update`` will,
    so the diff compares what would be stored, not the recipe's bare selector."""
    values = recipe_reapply.recipe_values(recipe, dep)
    proposed = recipe_reapply.proposed_row(dep, values)
    if pd_serving.is_pd(proposed):
        await _apply_pool_profiles(db, proposed, default_gpu_type=dep.gpu_type)
    if dep.gpu_type:
        try:
            gpu_profile, gpu_label_key = await resolve_gpu_type(db, dep.cluster_id, dep.gpu_type)
        except UnknownGpuTypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        apply_profile(proposed, gpu_profile, gpu_label_key)
    for field in ("node_selector", "tolerations", "gpu_resource_key", "pd_config"):
        values[field] = getattr(proposed, field)
    return values, proposed


@router.get("/{deployment_id}/recipe-diff")
async def recipe_diff(
    deployment_id: str,
    recipe_id: str | None = None,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """What re-applying the recipe would change, and what the rollout needs."""
    dep = await _deployment_or_404(db, deployment_id)
    recipe = await _recipe_for(db, dep, recipe_id)
    values, proposed = await _proposed(db, dep, recipe)
    try:
        check_rollout(proposed)
        blocked = None
    except ValueError as e:
        blocked = str(e)
    before, after = _registration_key(dep), _registration_key(proposed)
    return {
        "recipe": {"id": str(recipe.id), "name": recipe.name},
        "changes": recipe_reapply.changes(dep, values),
        "rollout": {
            "mode": serving_rollout.mode(proposed.runtime),
            "extra_gpus": serving_rollout.surge_gpus(proposed),
            "gpu_resource_key": proposed.gpu_resource_key,
            "blocked": blocked,
        },
        "reregister": before is not None and after is not None and before != after,
    }


@router.post("/{deployment_id}/reapply")
async def reapply_recipe(
    deployment_id: str,
    body: ReapplyRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> dict:
    """Overlay the recipe's serving fields on the deployment and roll it (same path as PUT).

    Instance fields (names, cluster, namespace, replicas, ingress, GPU type) stay;
    the GPU type's profile is applied again over the recipe's placement.
    """
    dep = await _deployment_or_404(db, deployment_id)
    recipe = await _recipe_for(db, dep, body.recipe_id)
    k8s = await k8s_for_cluster(db, dep.cluster_id)
    resolved, _ = await _proposed(db, dep, recipe)
    changed = [c["field"] for c in recipe_reapply.changes(dep, resolved)]
    values = recipe_reapply.recipe_values(recipe, dep)  # _apply_update folds the profile in itself
    if dep.gpu_type:
        values["gpu_type"] = dep.gpu_type
    dep.recipe_id = recipe.id
    out = await _apply_update(db, k8s, litellm, dep, values, user)
    await reconcile_deployments._record_event(
        db,
        dep,
        "RecipeApplied",
        "info",
        None,
        None,
        f"Re-applied recipe '{recipe.name}': {', '.join(changed) or 'no field changes'}",
    )
    await db.flush()
    return out


@router.delete("/{deployment_id}")
async def delete_deployment(
    deployment_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    k8s = await k8s_for_cluster(db, dep.cluster_id)
    try:
        await k8s.delete(dep.namespace, k8s_resource_names(dep))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s delete failed for %s", dep.model_name)
        # Continue to delete the row — orphan K8s resources are easier to clean than orphan DB rows.

    if getattr(dep, "router_stack_created", False) and getattr(dep, "router_stack_id", None):
        # The portal created this router for the serving; take it down with it.
        stack = await db.get(CustomLlmdStack, dep.router_stack_id)
        if stack is not None:
            try:
                await llmd_stacks.delete_stack(db, stack)
            except HTTPException as e:
                logger.warning("Router stack %s delete failed: %s", stack.name, e.detail)

    await db.delete(dep)
    return {"deleted": True, "id": deployment_id}


# ─── Deployment events (status transitions + alerts) ────────────


def _serialize_event(e) -> dict:
    return {
        "id": str(e.id),
        "deployment_id": str(e.deployment_id),
        "event_type": e.event_type,
        "severity": e.severity,
        "from_status": e.from_status,
        "to_status": e.to_status,
        "message": e.message,
        "seen": e.seen,
        "alert_sent": e.alert_sent,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/{deployment_id}/events")
async def list_deployment_events(
    deployment_id: str,
    limit: int = 100,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Most-recent-first list of status events for one deployment."""
    from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent

    result = await db.execute(
        select(CustomModelDeploymentEvent)
        .where(CustomModelDeploymentEvent.deployment_id == uuid.UUID(deployment_id))
        .order_by(CustomModelDeploymentEvent.created_at.desc())
        .limit(min(limit, 500))
    )
    return {"events": [_serialize_event(e) for e in result.scalars().all()]}


@router.post("/{deployment_id}/events/{event_id}/ack")
async def ack_deployment_event(
    deployment_id: str,
    event_id: str,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark a single event as seen so the UI can hide it from unread badges."""
    from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent

    result = await db.execute(
        select(CustomModelDeploymentEvent).where(CustomModelDeploymentEvent.id == uuid.UUID(event_id))
    )
    event = result.scalar_one_or_none()
    if not event or str(event.deployment_id) != deployment_id:
        raise HTTPException(status_code=404, detail="Event not found")
    event.seen = True
    await db.flush()
    return _serialize_event(event)
