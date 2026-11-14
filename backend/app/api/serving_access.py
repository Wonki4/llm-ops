"""Team namespace grants, serving inventory, and recipe-based operator actions."""

import hashlib
import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import model_deployments as deployments
from app.api import serving_recipes
from app.api.recipe_deployment import Count, DeployTarget, deploy_body
from app.api.serving_overview import load_topology, load_topology_workloads
from app.auth.deps import get_current_user, require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster
from app.db.models.custom_llmd_stack import CustomLlmdStack
from app.db.models.custom_model_deployment import CustomModelDeployment as Deployment
from app.db.models.custom_model_deployment_event import CustomModelDeploymentEvent as DeploymentEvent
from app.db.models.custom_serving_grant import CustomServingGrant as Grant
from app.db.models.custom_serving_grant import CustomServingGrantEvent as Event
from app.db.models.custom_serving_recipe import CustomServingRecipe as Recipe
from app.db.models.custom_user import CustomUser
from app.db.session import get_db, get_litellm_db
from app.services.clusters import NamespaceScope, validate_namespace
from app.services.pd_serving import pd_status_summary
from app.services.serving_access import deployment_scope_filter, require_scope, serving_scopes, team_grants_query
from app.services.serving_names import model_stem

router = APIRouter(tags=["serving-access"])
ADMIN = "/api/admin/serving/grants"


Role = Literal["viewer", "operator"]


class GrantScope(BaseModel):
    cluster_id: uuid.UUID | None
    namespace: str

    @field_validator("namespace")
    @classmethod
    def concrete_namespace(cls, value):
        name = validate_namespace(value)
        if not name:
            raise ValueError("A namespace is required")
        return name


class GrantBody(GrantScope):
    team_id: str = Field(min_length=1)
    role: Role


class GrantChange(GrantScope):
    previous_role: Role | None
    role: Role | None


class TeamGrantChanges(BaseModel):
    team_id: str = Field(min_length=1)
    changes: list[GrantChange] = Field(min_length=1)

    @field_validator("changes")
    @classmethod
    def check_unique_scopes(cls, changes):
        if len({(c.cluster_id, c.namespace) for c in changes}) != len(changes):
            raise ValueError("Each namespace may only be changed once per cluster")
        return changes


@router.get(ADMIN)
async def list_grants(user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db),
                      team_id: str | None = None) -> dict:
    grant_query = select(Grant.__table__)
    history_query = select(Event.__table__)
    if team_id is not None:
        grant_query = grant_query.where(Grant.team_id == team_id)
        history_query = history_query.where(Event.team_id == team_id)
    grants = await db.execute(grant_query.order_by(Grant.namespace, Grant.team_id))
    history = await db.execute(history_query.order_by(Event.created_at.desc(), Event.id.desc()).limit(50))
    return {"grants": [dict(g) for g in grants.mappings()], "history": [dict(h) for h in history.mappings()]}


@router.get(ADMIN + "/teams")
async def team_grant_counts(user: CustomUser = Depends(require_super_user), db: AsyncSession = Depends(get_db)):
    counts = await db.execute(select(Grant.team_id, func.count().label("count")).group_by(Grant.team_id))
    return {"teams": [dict(row) for row in counts.mappings()]}


async def require_grant_team(litellm_db: AsyncSession, team_id: str):
    team = await litellm_db.scalar(text('SELECT team_id FROM "LiteLLM_TeamTable" WHERE team_id = :id'),
                                   {"id": team_id})
    if team is None:
        raise HTTPException(404, "Team not found")


@router.patch(ADMIN)
async def change_team_grants(body: TeamGrantChanges, user: CustomUser = Depends(require_super_user),
                             db: AsyncSession = Depends(get_db), litellm_db: AsyncSession = Depends(get_litellm_db)):
    # Removal is allowed even if the team was deleted in LiteLLM.
    if any(c.role is not None for c in body.changes):
        await require_grant_team(litellm_db, body.team_id)
    for cluster_id in {c.cluster_id for c in body.changes if c.cluster_id is not None and c.role is not None}:
        if await db.get(CustomK8sCluster, cluster_id) is None:
            raise HTTPException(404, "Cluster not found")
    # Acquire locks in a stable order and compare against the role seen by the editor.
    # get_db rolls back the whole change set on a conflict.
    for c in sorted(body.changes, key=lambda change: (str(change.cluster_id or ""), change.namespace)):
        if c.role == c.previous_role:
            continue
        scope = dict(team_id=body.team_id, cluster_id=c.cluster_id, namespace=c.namespace)
        if c.previous_role is None:
            stmt = insert(Grant).values(**scope, role=c.role).on_conflict_do_nothing(
                constraint="uq_serving_grant_scope"
            )
        elif c.role is None:
            stmt = delete(Grant).filter_by(**scope, role=c.previous_role)
        else:
            stmt = update(Grant).filter_by(**scope, role=c.previous_role).values(role=c.role)
        if await db.scalar(stmt.returning(Grant.id)) is None:
            current = await db.scalar(select(Grant.role).filter_by(**scope))
            if current != c.role:
                raise HTTPException(409, "Namespace access has changed. Reopen the team editor and try again.")
            continue
        db.add(Event(**scope, role=c.role, actor=user.user_id))
    return {"saved": True}


@router.put(ADMIN)
async def save_grant(body: GrantBody, user: CustomUser = Depends(require_super_user),
                     db: AsyncSession = Depends(get_db), litellm_db: AsyncSession = Depends(get_litellm_db)) -> dict:
    if body.cluster_id is not None and await db.get(CustomK8sCluster, body.cluster_id) is None:
        raise HTTPException(404, "Cluster not found")
    await require_grant_team(litellm_db, body.team_id)
    values = body.model_dump()
    stmt = insert(Grant).values(**values)
    changed_id = await db.scalar(stmt.on_conflict_do_update(
        constraint="uq_serving_grant_scope", set_={"role": body.role}, where=Grant.role != body.role
    ).returning(Grant.id))
    if changed_id is not None:
        db.add(Event(**values, actor=user.user_id))
    return {"saved": True}


@router.delete(ADMIN + "/{grant_id}")
async def revoke_grant(grant_id: uuid.UUID, user: CustomUser = Depends(require_super_user),
                       db: AsyncSession = Depends(get_db)) -> dict:
    grant = (await db.execute(delete(Grant).where(Grant.id == grant_id)
                              .returning(Grant.team_id, Grant.cluster_id, Grant.namespace))).mappings().first()
    if grant:
        db.add(Event(**grant, role=None, actor=user.user_id))
    return {"revoked": True}


@router.get("/api/serving/scopes")
async def my_scopes(scopes: list[dict] = Depends(serving_scopes)) -> dict:
    return {"scopes": scopes}


async def authorized_namespace(namespace: str, cluster_id: uuid.UUID | None = None,
                               scopes: list[dict] = Depends(serving_scopes),
                               db: AsyncSession = Depends(get_db)) -> NamespaceScope:
    require_scope(scopes, cluster_id, namespace)
    cluster = await db.get(CustomK8sCluster, cluster_id) if cluster_id else None
    if cluster_id and cluster is None:
        raise HTTPException(404, "Cluster not found")
    return NamespaceScope(cluster, namespace)


@router.get("/api/serving/topology")
async def team_topology(scope: NamespaceScope = Depends(authorized_namespace), db: AsyncSession = Depends(get_db),
                        litellm: LiteLLMClient = Depends(get_litellm_client)) -> dict:
    return await load_topology(db, litellm, scope.cluster_id or "default", scope.namespace, True, permitted=scope)


@router.get("/api/serving/topology/workloads")
async def team_workloads(kind: Literal["ingress", "service"], name: str,
                         scope: NamespaceScope = Depends(authorized_namespace), db: AsyncSession = Depends(get_db)):
    return await load_topology_workloads(db, scope, kind, name)


_DEPLOYMENT_COLUMNS = (
    Deployment.id, Deployment.cluster_id, Deployment.namespace, Deployment.model_name, Deployment.litellm_model_name,
    Deployment.status, Deployment.replicas, Deployment.ready_replicas, Deployment.serving_mode, Deployment.pd_status,
    Deployment.engine, Deployment.last_synced_at,
)


def deployment_summary(row) -> dict:
    data = dict(row)
    data["pool_status"] = pd_status_summary(data.pop("pd_status"))
    return data


@router.get("/api/teams/{team_id}/serving-deployments")
async def team_serving_inventory(team_id: str, user: CustomUser = Depends(get_current_user),
                                 db: AsyncSession = Depends(get_db), litellm_db: AsyncSession = Depends(get_litellm_db)):
    grants = (await team_grants_query(user, litellm_db, team_id)).subquery()
    query = (select(grants.c.cluster_id.label("scope_cluster"), grants.c.namespace.label("scope_namespace"),
                    grants.c.name, grants.c.role, *_DEPLOYMENT_COLUMNS)
             .select_from(grants).outerjoin(Deployment, (
                 Deployment.cluster_id.is_not_distinct_from(grants.c.cluster_id)
                 & (Deployment.namespace == grants.c.namespace)))
             .order_by(grants.c.name.nulls_first(), grants.c.namespace, Deployment.model_name, Deployment.id))
    scopes = {}
    for row in (await db.execute(query)).mappings():
        key = (row["scope_cluster"], row["scope_namespace"])
        scope = scopes.setdefault(key, dict(cluster_id=key[0], namespace=key[1], cluster_name=row["name"],
                                            role=row["role"], deployments=[]))
        if row["id"] is not None:
            scope["deployments"].append(deployment_summary({col.key: row[col.key] for col in _DEPLOYMENT_COLUMNS}))
    return {"scopes": list(scopes.values())}


@router.get("/api/serving/deployments")
async def team_deployments(namespace: str, cluster_id: uuid.UUID | None = None,
                           scopes: list[dict] = Depends(serving_scopes), db: AsyncSession = Depends(get_db)) -> dict:
    require_scope(scopes, cluster_id, namespace)
    rows = await db.execute(select(*_DEPLOYMENT_COLUMNS).where(
        Deployment.cluster_id == cluster_id, Deployment.namespace == namespace).order_by(Deployment.model_name))
    return {"deployments": [deployment_summary(r) for r in rows.mappings()]}


@router.get("/api/serving/deployments/{deployment_id}")
async def team_deployment(deployment_id: uuid.UUID, scopes: list[dict] = Depends(serving_scopes),
                          db: AsyncSession = Depends(get_db)) -> dict:
    query = select(
        *_DEPLOYMENT_COLUMNS, Deployment.image, Deployment.gpu_type, Deployment.gpu_count,
        Deployment.created_at, Deployment.recipe_id, Deployment.ingress_host,
        Deployment.pd_config["prefill"]["replicas"].as_integer().label("prefill_replicas"),
        Deployment.pd_config["decode"]["replicas"].as_integer().label("decode_replicas"),
    ).where(Deployment.id == deployment_id, deployment_scope_filter(scopes))
    row = (await db.execute(query)).mappings().first()
    if row is None:
        raise HTTPException(404, "Deployment not found")
    events = await db.execute(select(DeploymentEvent.created_at, DeploymentEvent.event_type, DeploymentEvent.severity,
                                     DeploymentEvent.from_status, DeploymentEvent.to_status)
                              .where(DeploymentEvent.deployment_id == deployment_id)
                              .order_by(DeploymentEvent.created_at.desc(), DeploymentEvent.id.desc()).limit(30))
    return {**deployment_summary(row), "events": [dict(event) for event in events.mappings()]}


async def operator_scopes(scopes: list[dict] = Depends(serving_scopes)) -> list[dict]:
    allowed = [s for s in scopes if s["role"] == "operator"]
    if not allowed:
        raise HTTPException(403, "Serving operator access is required")
    return allowed


async def managed_deployment(deployment_id: uuid.UUID, scopes: list[dict] = Depends(operator_scopes),
                             db: AsyncSession = Depends(get_db)) -> Deployment:
    dep = await db.scalar(select(Deployment).where(Deployment.id == deployment_id, deployment_scope_filter(scopes))
                          .with_for_update())
    if dep is None:
        raise HTTPException(404, "Deployment not found")
    return dep


def operator_ingress(model_name: str, cluster_id: uuid.UUID | None, namespace: str) -> str:
    identity = f"{cluster_id}/{namespace}/{model_name}"
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"{model_stem(model_name)[:48].rstrip('-')}-{suffix}.{settings.effective_ingress_domain}"


@router.get("/api/serving/recipes")
async def team_recipes(namespace: str, cluster_id: uuid.UUID | None = None,
                       scopes: list[dict] = Depends(operator_scopes), db: AsyncSession = Depends(get_db)):
    require_scope(scopes, cluster_id, namespace)
    rows = await db.execute(select(Recipe.id, Recipe.name, Recipe.hf_repo_id, Recipe.engine, Recipe.serving_mode,
                                   Recipe.pd_config["prefill"]["replicas"].as_integer().label("prefill_replicas"),
                                   Recipe.pd_config["decode"]["replicas"].as_integer().label("decode_replicas"))
                            .order_by(Recipe.name))
    return {"recipes": [dict(row) for row in rows.mappings()]}


class OperatorDeploy(GrantScope):
    model_config = ConfigDict(extra="forbid")
    recipe_id: uuid.UUID
    model_name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    replicas: Count | None = None
    prefill_replicas: Count | None = None
    decode_replicas: Count | None = None


@router.post("/api/serving/deployments", status_code=201)
async def team_create_deployment(body: OperatorDeploy, scopes: list[dict] = Depends(operator_scopes),
                                 user: CustomUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                                 litellm: LiteLLMClient = Depends(get_litellm_client)):
    require_scope(scopes, body.cluster_id, body.namespace)
    recipe = await db.get(Recipe, body.recipe_id)
    if recipe is None:
        raise HTTPException(404, "Serving recipe not found")
    # Published aliases may group several registrations. Creating a workload must not join another model's traffic.
    reserved = await db.scalar(select(Deployment.id).where(Deployment.litellm_model_name == body.model_name).limit(1))
    if reserved:
        raise HTTPException(409, "Model name is already in use")
    try:
        registered = await litellm.get_model_info()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not check model name availability in LiteLLM") from exc
    if any(m.get("model_name") == body.model_name for m in registered):
        raise HTTPException(409, "Model name is already in use")
    target = DeployTarget(
        **body.model_dump(exclude={"recipe_id"}),
        ingress_host=operator_ingress(body.model_name, body.cluster_id, body.namespace),
        ingress_class=((recipe.pd_config or {}).get("router") or {}).get("ingress_class")
        or settings.llmd_ingress_class or "nginx",
    )
    try:
        request = deploy_body(serving_recipes._serialize(recipe), target)
    except ValueError as exc:
        raise HTTPException(422, "Recipe and instance settings are incompatible") from exc
    created = await deployments.create_deployment(request, user, db)
    return {"id": created["id"]}


@router.put("/api/serving/deployments/{deployment_id}/replicas")
async def team_scale(body: deployments.ReplicaRequest, dep: Deployment = Depends(managed_deployment),
                     user: CustomUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await deployments.scale_deployment_replicas(db, dep, body, user)


@router.put("/api/serving/deployments/{deployment_id}/pools/{role}/replicas")
async def team_scale_pool(role: Literal["prefill", "decode"], body: deployments.ReplicaRequest,
                          dep: Deployment = Depends(managed_deployment), user: CustomUser = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    return await deployments.scale_pool_replicas(db, dep, role, body, user)


async def check_owned_router_scope(db, dep):
    if dep.router_stack_created and dep.router_stack_id:
        stack = await db.get(CustomLlmdStack, dep.router_stack_id, with_for_update=True)
        if stack and (stack.cluster_id, stack.namespace) != (dep.cluster_id, dep.namespace):
            raise HTTPException(409, "Deployment router belongs to another namespace")


class OperatorReapply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: uuid.UUID


@router.post("/api/serving/deployments/{deployment_id}/reapply")
async def team_reapply(body: OperatorReapply, dep: Deployment = Depends(managed_deployment),
                       user: CustomUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                       litellm: LiteLLMClient = Depends(get_litellm_client)):
    await check_owned_router_scope(db, dep)
    recipe = await deployments._recipe_for(db, dep, str(body.recipe_id))
    await deployments.apply_deployment_recipe(db, dep, recipe, litellm, user)
    return {"id": dep.id}


@router.delete("/api/serving/deployments/{deployment_id}")
async def team_delete(dep: Deployment = Depends(managed_deployment), db: AsyncSession = Depends(get_db),
                      litellm: LiteLLMClient = Depends(get_litellm_client)):
    await check_owned_router_scope(db, dep)
    result = await deployments.remove_deployment(db, dep, strict_cleanup=True)
    if dep.litellm_model_id:
        try:
            await litellm.delete_model(dep.litellm_model_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise HTTPException(502, "Could not remove the deployment's LiteLLM registration") from exc
        except httpx.RequestError as exc:
            raise HTTPException(502, "Could not reach LiteLLM to remove the deployment registration") from exc
    return result
