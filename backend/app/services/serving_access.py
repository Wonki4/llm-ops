"""Resolve serving grants from current team membership for each portal request."""

import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import and_, exists, false, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.config import settings
from app.db.models.custom_k8s_cluster import CustomK8sCluster as Cluster
from app.db.models.custom_model_deployment import CustomModelDeployment as Deployment
from app.db.models.custom_serving_grant import CustomServingGrant as Grant
from app.db.models.custom_user import CustomUser, GlobalRole
from app.db.session import get_db, get_litellm_db

# The portal supports teams populated through the proxy and older array-based memberships.
_MEMBER_TEAMS = """
    SELECT t.team_id FROM "LiteLLM_TeamTable" t
    WHERE NOT COALESCE(t.blocked, false) AND (
        :uid = ANY(t.admins) OR :uid = ANY(t.members)
        OR t.members_with_roles @> jsonb_build_array(jsonb_build_object('user_id', CAST(:uid AS text)))
        OR EXISTS (SELECT 1 FROM "LiteLLM_UserTable" u WHERE u.user_id = :uid AND t.team_id = ANY(u.teams))
        OR EXISTS (SELECT 1 FROM "LiteLLM_TeamMembership" m WHERE m.user_id = :uid AND m.team_id = t.team_id)
    )
"""


async def team_grants_query(user: CustomUser, litellm_db: AsyncSession, team_id: str | None = None):
    query = (select(Grant.cluster_id, Grant.namespace, Cluster.name, Cluster.is_default,
                    Cluster.namespace.label("default_ns"), Grant.role)
             .outerjoin(Cluster, Cluster.id == Grant.cluster_id))
    if team_id is not None:
        query = query.where(Grant.team_id == team_id)
    if user.global_role == GlobalRole.SUPER_USER:
        return query

    membership_query = _MEMBER_TEAMS
    params = {"uid": user.user_id}
    if team_id is not None:
        membership_query += " AND t.team_id = :team_id"
        params["team_id"] = team_id
    ids = (await litellm_db.execute(text(membership_query), params)).scalars().all()
    if team_id is not None and not ids:
        raise HTTPException(403, "No serving access to this team")
    # Exclude expired memberships before the cleanup worker removes them from LiteLLM.
    expired = text("""
        SELECT team_id FROM custom_team_membership
        WHERE user_id = :uid AND (status = 'expired' OR expires_at <= LOCALTIMESTAMP)
    """).bindparams(uid=user.user_id).columns(team_id=Grant.team_id.type).subquery()
    return query.where(Grant.team_id.in_(ids), ~exists(select(1).where(expired.c.team_id == Grant.team_id)))


async def serving_scopes(user: CustomUser = Depends(get_current_user), db: AsyncSession = Depends(get_db),
                         litellm_db: AsyncSession = Depends(get_litellm_db)) -> list[dict]:
    if user.global_role == GlobalRole.SUPER_USER:
        scopes = select(Grant.cluster_id, Grant.namespace).union(
            select(Deployment.cluster_id, Deployment.namespace), select(Cluster.id, Cluster.namespace)
        ).subquery()
        rows = (await db.execute(select(scopes.c.cluster_id, scopes.c.namespace, Cluster.name,
                                        Cluster.is_default, Cluster.namespace.label("default_ns"))
                                 .outerjoin(Cluster, Cluster.id == scopes.c.cluster_id))).all()
        rows = [(*row, "operator") for row in rows]
        if not any(cid is None and ns == settings.k8s_default_namespace for cid, ns, *_ in rows):
            rows.append((None, settings.k8s_default_namespace, None, None, None, "operator"))
    else:
        rows = (await db.execute(await team_grants_query(user, litellm_db))).all()

    effective = {}
    for cid, ns, name, default, default_ns, role in rows:
        key = (cid, ns)
        if key not in effective or role == "operator":
            effective[key] = dict(cluster_id=cid, cluster_name=name, namespace=ns, role=role,
                                  is_default=bool(default and ns == default_ns))
    return sorted(effective.values(), key=lambda s: (
        not s["is_default"], (s["cluster_id"], s["namespace"]) != (None, settings.k8s_default_namespace),
        s["cluster_name"] or "", s["namespace"],
    ))


def require_scope(scopes: list[dict], cluster_id: uuid.UUID | None, namespace: str) -> dict:
    for scope in scopes:
        if (scope["cluster_id"], scope["namespace"]) == (cluster_id, namespace):
            return scope
    raise HTTPException(403, "No serving access to this namespace")


def deployment_scope_filter(scopes: list[dict]):
    """Keep NULL cluster IDs bound to the portal cluster in SQL too."""
    return or_(false(), *(and_(Deployment.cluster_id == s["cluster_id"], Deployment.namespace == s["namespace"])
                          for s in scopes))
