"""External API endpoints - authenticated via API key for external system integration."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
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


@router.put("/teams/{team_id}/budget")
async def update_team_budget(
    team_id: str,
    body: UpdateTeamBudgetRequest,
    _key: str = Depends(verify_external_api_key),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    litellm_db: AsyncSession = Depends(get_litellm_db),
) -> dict:
    """Update a team's budget settings. Requires X-Api-Key header.

    Fields:
    - max_budget: maximum budget (dollars). null to remove limit.
    - budget_duration: reset period (e.g. "30d", "1mo"). null to remove.
    - tpm_limit: tokens-per-minute limit. null to remove.
    - rpm_limit: requests-per-minute limit. null to remove.
    """
    from sqlalchemy import text

    # Verify team exists
    result = await litellm_db.execute(
        text('SELECT team_id, team_alias FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    team_row = result.mappings().first()
    if not team_row:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")

    # Build update kwargs (only include explicitly set fields)
    update_kwargs: dict = {}
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"status": "unchanged", "team_id": team_id}

    if "max_budget" in updates:
        update_kwargs["max_budget"] = updates["max_budget"]
    if "budget_duration" in updates:
        update_kwargs["budget_duration"] = updates["budget_duration"]
    if "tpm_limit" in updates:
        update_kwargs["tpm_limit"] = updates["tpm_limit"]
    if "rpm_limit" in updates:
        update_kwargs["rpm_limit"] = updates["rpm_limit"]

    await litellm.update_team(team_id, **update_kwargs)

    logger.info(
        "External API: updated team %s (%s) budget: %s",
        team_id,
        team_row["team_alias"],
        update_kwargs,
    )

    return {
        "status": "updated",
        "team_id": team_id,
        "team_alias": team_row["team_alias"],
        **update_kwargs,
    }
