"""Admin endpoints for managing K8s-backed LLM deployments.

PR-A scope: create, list, get, delete. Status only reflects whatever the
worker has last synced — the worker is added in PR-B.
"""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_super_user
from app.clients.k8s import K8sClient, K8sNotConfigured, get_k8s_client  # noqa: F401
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.models.custom_external_serving import CustomExternalServing
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment
from app.db.models.custom_serving_recipe import CustomServingRecipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db
from app.services import llmd_stacks, pd_serving
from app.services.clusters import k8s_for_cluster
from app.services.external_servings import scan_clusters
from app.services.gpu_profile_store import UnknownGpuTypeError, cluster_label_key, list_profiles, resolve_gpu_type
from app.services.gpu_profiles import apply_profile, match_profile, validate_profile_name
from app.services.llmd_links import portal_server, stacks_for_server
from app.services.llmd_manifests import default_llmd_values, stack_selector
from app.services.model_deployment_manifests import build_all, k8s_resource_names
from app.services.recipe_import import build_recipe_draft
from app.services.serving_engines import DEFAULT_IMAGES, ServingEngine, default_image, validate_engine_args
from app.services.serving_probes import validate_probes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/model-deployments", tags=["model-deployments"])


DEFAULT_VLLM_IMAGE = DEFAULT_IMAGES["vllm"]  # kept for existing references


class CreateDeploymentRequest(BaseModel):
    model_name: str
    cluster_id: str | None = None  # registered K8s cluster; None = portal default
    namespace: str = "default"
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
    pvc_name: str | None = None
    pvc_mount_path: str | None = None
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
    image: str | None = None
    replicas: int | None = Field(None, ge=0)
    gpu_count: int | None = Field(None, ge=0)
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    node_selector: dict | None = None
    tolerations: list | None = None
    pvc_name: str | None = None
    pvc_mount_path: str | None = None
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
        "pvc_name": d.pvc_name,
        "pvc_mount_path": d.pvc_mount_path,
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
    """Live-scan all clusters for vLLM/SGLang deployments not managed by the portal."""
    targets: list[tuple[str | None, str, K8sClient]] = [(None, "default", K8sClient())]
    clusters = (await db.execute(select(CustomK8sCluster))).scalars().all()
    for row in clusters:
        targets.append((str(row.id), row.name, await k8s_for_cluster(db, row.id)))

    servings, errors = await scan_clusters(targets)

    regs = (await db.execute(select(CustomExternalServing))).scalars().all()
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
    return {
        **_serialize(dep, await _recipe_names(db, [dep])),
        "llmd_stacks": linked,
        "router_stack": await _router_stack_summary(db, dep),
    }


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
    epp_registry = router.get("epp_registry") or settings.llmd_epp_image_registry
    epp_repository = router.get("epp_repository") or settings.llmd_epp_image_repository
    epp_tag = router.get("epp_tag") or settings.llmd_epp_image_tag
    values = default_llmd_values(
        dep.model_name,
        epp_registry=epp_registry,
        epp_repository=epp_repository,
        epp_tag=epp_tag,
        serving_mode="pd",
        pd_router=router,
    )
    try:
        stack = await llmd_stacks.create_stack(
            db,
            name=name,
            target_model_name=dep.model_name,
            cluster_id=dep.cluster_id,
            namespace=dep.namespace,
            values=values,
            user_id=user_id,
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
    try:
        gpu_profile, gpu_label_key = await resolve_gpu_type(db, cluster_uuid, body.gpu_type)
    except UnknownGpuTypeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    dep = CustomModelDeployment(
        id=uuid.uuid4(),
        model_name=body.model_name,
        cluster_id=uuid.UUID(body.cluster_id) if body.cluster_id else None,
        namespace=body.namespace,
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
        pvc_name=body.pvc_name,
        pvc_mount_path=body.pvc_mount_path,
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
    apply_profile(dep, gpu_profile, gpu_label_key)
    db.add(dep)
    await db.flush()
    await db.refresh(dep)

    # Apply to K8s (P/D: prefill + decode pools, no Ingress — the router is the entry)
    try:
        await k8s.create_or_patch(dep.namespace, build_all(dep, sidecar_image=settings.llmd_sidecar_image))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s apply failed for %s", dep.model_name)
        raise HTTPException(status_code=502, detail="Failed to apply K8s resources; check logs")

    if pd_serving.is_pd(dep):
        await _attach_router(db, dep, body, user.user_id)

    return _serialize(dep)


@router.put("/{deployment_id}")
async def update_deployment(
    deployment_id: str,
    body: UpdateDeploymentRequest,
    user: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update mutable fields and reapply K8s manifests (rolls Deployment).

    Update covers both upgrade (image / args) and scale (replicas, gpu_count).
    """
    result = await db.execute(select(CustomModelDeployment).where(CustomModelDeployment.id == uuid.UUID(deployment_id)))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    k8s = await k8s_for_cluster(db, dep.cluster_id)
    updates = body.model_dump(exclude_unset=True)
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
        await k8s.create_or_patch(dep.namespace, build_all(dep, sidecar_image=settings.llmd_sidecar_image))
    except K8sNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        logger.exception("K8s apply failed for %s", dep.model_name)
        raise HTTPException(status_code=502, detail="Failed to apply K8s resources; check logs")

    return _serialize(dep)


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
