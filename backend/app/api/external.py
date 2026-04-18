"""External API endpoints - authenticated via API key for external system integration."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.litellm import LiteLLMClient, get_litellm_client
from app.config import settings
from app.db.session import get_litellm_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/external", tags=["external"])


async def verify_external_api_key(x_api_key: str = Header(...)) -> str:
    """Verify external API key from X-Api-Key header."""
    if not settings.external_api_key:
        raise HTTPException(status_code=503, detail="External API not configured")
    if x_api_key != settings.external_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


class UpdateTeamBudgetRequest(BaseModel):
    max_budget: float | None = None
    budget_duration: str | None = None  # e.g. "30d", "1mo"
    tpm_limit: int | None = None
    rpm_limit: int | None = None


async def _resolve_team(
    litellm_db: AsyncSession,
    *,
    team_id: str | None = None,
    team_alias: str | None = None,
) -> dict:
    """Return the team row matching team_id or team_alias.

    Raises 404 when no match, 409 when team_alias matches multiple teams.
    Exactly one of team_id / team_alias must be provided.
    """
    if team_id is not None:
        result = await litellm_db.execute(
            text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
            {"team_id": team_id},
        )
        row = result.mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
        return dict(row)

    result = await litellm_db.execute(
        text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" WHERE team_alias = :team_alias'),
        {"team_alias": team_alias},
    )
    rows = list(result.mappings())
    if not rows:
        raise HTTPException(status_code=404, detail=f"Team with name '{team_alias}' not found")
    if len(rows) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Multiple teams share the name '{team_alias}'. Use team_id instead.",
                "team_ids": [r["team_id"] for r in rows],
            },
        )
    return dict(rows[0])


async def _apply_team_budget_update(
    litellm: LiteLLMClient,
    team_row: dict,
    body: UpdateTeamBudgetRequest,
) -> dict:
    """Apply budget update to LiteLLM and log. Returns response payload."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {
            "status": "unchanged",
            "team_id": team_row["team_id"],
            "team_alias": team_row["team_alias"],
        }

    update_kwargs: dict = {}
    for key in ("max_budget", "budget_duration", "tpm_limit", "rpm_limit"):
        if key in updates:
            update_kwargs[key] = updates[key]

    await litellm.update_team(team_row["team_id"], **update_kwargs)

    logger.info(
        "External API: updated team %s (%s) budget: %s",
        team_row["team_id"],
        team_row["team_alias"],
        update_kwargs,
    )

    return {
        "status": "updated",
        "team_id": team_row["team_id"],
        "team_alias": team_row["team_alias"],
        **update_kwargs,
    }


@router.put("/teams/{team_id}/budget")
async def update_team_budget(
    team_id: str,
    body: UpdateTeamBudgetRequest,
    _key: str = Depends(verify_external_api_key),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update a team's budget settings by team_id. Requires X-Api-Key header.

    Fields:
    - max_budget: maximum budget (dollars). null to remove limit.
    - budget_duration: reset period (e.g. "30d", "1mo"). null to remove.
    - tpm_limit: tokens-per-minute limit. null to remove.
    - rpm_limit: requests-per-minute limit. null to remove.
    """
    team_row = await _resolve_team(litellm_db, team_id=team_id)
    return await _apply_team_budget_update(litellm, team_row, body)


@router.put("/teams/by-name/{team_alias}/budget")
async def update_team_budget_by_name(
    team_alias: str,
    body: UpdateTeamBudgetRequest,
    _key: str = Depends(verify_external_api_key),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update a team's budget settings by team name (team_alias).

    Returns 409 Conflict if multiple teams share the same name; use team_id
    in that case.
    """
    team_row = await _resolve_team(litellm_db, team_alias=team_alias)
    return await _apply_team_budget_update(litellm, team_row, body)
