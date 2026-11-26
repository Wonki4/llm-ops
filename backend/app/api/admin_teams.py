"""Live team grants; LiteLLM remains the authority, without a portal replica."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.teams import _TEAM_COLUMNS, _get_unified_access_groups, _row_to_team
from app.auth.deps import require_super_user
from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.db.models.custom_user import CustomUser
from app.db.session import get_litellm_db
from app.services.personal_access import (
    ALL_PROXY_MODELS,
    NO_DEFAULT_MODELS,
    access_groups_from_model_info,
    models_for,
    validate_selection,
)

router = APIRouter(prefix="/api/admin/teams", tags=["admin-teams"])


def team_access(team: dict, rows: list[dict], inherited: list[dict]) -> dict:
    models = team.get("models") or []
    groups = access_groups_from_model_info(rows)
    names = sorted({r["model_name"] for r in rows if r.get("model_name")})
    mode = (
        "all"
        if not models or ALL_PROXY_MODELS in models
        else "none"
        if set(models) == {NO_DEFAULT_MODELS}
        else "custom"
    )
    sources: dict[str, list[str]] = {}
    for name in names:
        via = []
        if mode == "all":
            via.append("all")
        if name in models:
            via.append("direct")
        via += [f"group:{group}" for group in models if name in groups.get(group, [])]
        via += [
            f"inherited:{g['access_group_name']}"
            for g in inherited
            if name in g["models"] or ALL_PROXY_MODELS in g["models"]
        ]
        if via:
            sources[name] = via
    return {
        "mode": mode,
        "models": models,
        "selection": [m for m in models if m not in {ALL_PROXY_MODELS, NO_DEFAULT_MODELS}],
        "inherited_groups": inherited,
        "effective_models": [{"model_name": n, "via": via} for n, via in sources.items()],
        "available_models": names,
        "available_groups": groups,
    }


async def live_models(litellm):
    try:
        return await litellm.get_model_info()
    except Exception as exc:
        raise HTTPException(502, "LiteLLM model grants are unavailable") from exc


@router.get("")
async def list_teams(
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    model_name: str | None = None,
    access_group: str | None = None,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_litellm_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    where = "(LOWER(t.team_alias) LIKE :search OR LOWER(t.team_id) LIKE :search)"
    params: dict = {"search": f"%{q.strip().lower()}%", "limit": page_size, "offset": (page - 1) * page_size}
    rows = await live_models(litellm)
    groups = access_groups_from_model_info(rows)
    if access_group:
        where += " AND :access_group = ANY(t.models)"
        params["access_group"] = access_group
    if model_name:
        entries = [model_name, ALL_PROXY_MODELS, *[g for g, names in groups.items() if model_name in names]]
        where += """ AND (COALESCE(cardinality(t.models), 0) = 0 OR t.models && CAST(:entries AS TEXT[])
            OR EXISTS (SELECT 1 FROM "LiteLLM_AccessGroupTable" g WHERE g.access_group_id = ANY(t.access_group_ids)
                       AND ( :model = ANY(g.access_model_names) OR :all_models = ANY(g.access_model_names))))"""
        params.update(entries=entries, model=model_name, all_models=ALL_PROXY_MODELS)
    total = (await db.execute(text(f'SELECT COUNT(*) FROM "LiteLLM_TeamTable" t WHERE {where}'), params)).scalar_one()
    result = await db.execute(
        text(
            f'SELECT {_TEAM_COLUMNS} FROM "LiteLLM_TeamTable" t WHERE {where} '
            "ORDER BY LOWER(COALESCE(t.team_alias,t.team_id)), t.team_id LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    teams = [_row_to_team(r) for r in result.mappings()]
    inherited = await _get_unified_access_groups(db, [t["team_id"] for t in teams])
    return {
        "teams": [{**t, "access": team_access(t, rows, inherited.get(t["team_id"], []))} for t in teams],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def get_access(team_id, db, litellm):
    result = await db.execute(
        text('SELECT team_id, team_alias, models FROM "LiteLLM_TeamTable" WHERE team_id = :id'), {"id": team_id}
    )
    team = result.mappings().first()
    if not team:
        raise HTTPException(404, "Team not found")
    inherited = await _get_unified_access_groups(db, [team_id])
    return {
        "team_id": team_id,
        "team_alias": team["team_alias"],
        **team_access(dict(team), await live_models(litellm), inherited.get(team_id, [])),
    }


@router.get("/{team_id}/access")
async def read_access(
    team_id: str,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_litellm_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    return await get_access(team_id, db, litellm)


class AccessUpdate(BaseModel):
    mode: Literal["none", "all", "custom"]
    selection: list[str] = []
    expected_models: list[str]


@router.put("/{team_id}/access")
async def update_access(
    team_id: str,
    body: AccessUpdate,
    _admin: CustomUser = Depends(require_super_user),
    db: AsyncSession = Depends(get_litellm_db),
    litellm: LiteLLMClient = Depends(get_litellm_client),
):
    current = await get_access(team_id, db, litellm)
    if sorted(current["models"]) != sorted(body.expected_models):
        raise HTTPException(409, "Team grants changed in LiteLLM. Refresh before saving.")
    try:
        models = models_for(body.mode, body.selection)
        if body.mode == "custom":
            validate_selection(
                models, model_names=set(current["available_models"]), access_groups=set(current["available_groups"])
            )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        await litellm.update_team(team_id, models=models)
    except Exception as exc:
        raise HTTPException(502, "LiteLLM team update failed; refresh before retrying") from exc
    # Unified access_group_ids, budgets, member grants and keys are not overwritten.
    return {"team_id": team_id, "models": models}
