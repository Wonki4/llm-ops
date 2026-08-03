"""Read the caller's own in-team budget and spend from the LiteLLM/portal DBs.

Scope is strictly the caller (user_id): the member's effective team_member
budget row + their TeamMembership.spend. Never team-wide totals.
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def _strict_hidden(portal_db: AsyncSession) -> set[str]:
    """team_ids strictly hidden from members (portal setting hidden_teams_strict)."""
    value = (
        await portal_db.execute(
            text("SELECT value FROM custom_portal_settings WHERE key = 'hidden_teams_strict'")
        )
    ).scalar()
    return set(json.loads(value)) if value else set()


async def list_visible_teams(portal_db: AsyncSession, litellm_db: AsyncSession, user_id: str) -> list[dict]:
    """Teams the caller belongs to, minus strictly-hidden ones (member view).

    Mirrors the portal's my-teams visibility: discovery-hidden teams remain,
    only hidden_teams_strict disappear. (The MCP caller is always treated as a
    regular member — no super-user elevation here.)
    """
    rows = (
        await litellm_db.execute(
            text(
                'SELECT t.team_id, t.team_alias FROM "LiteLLM_TeamTable" t '
                "WHERE t.team_id = ANY("
                '    SELECT unnest(u.teams) FROM "LiteLLM_UserTable" u WHERE u.user_id = :uid'
                ") ORDER BY t.team_alias"
            ),
            {"uid": user_id},
        )
    ).mappings().all()
    strict = await _strict_hidden(portal_db)
    return [
        {"team_id": r["team_id"], "team_alias": r["team_alias"]}
        for r in rows
        if r["team_id"] not in strict
    ]


async def member_budget_usage(litellm_db: AsyncSession, team_id: str, user_id: str) -> dict | None:
    """The caller's own in-team budget + spend for a team, or None if not a member.

    budget = the member's effective team_member budget row (their dedicated
    TeamMembership.budget_id, else the team default metadata.team_member_budget_id).
    spend = TeamMembership.spend. remaining = max_budget - spend (None if unlimited).
    """
    membership = (
        await litellm_db.execute(
            text(
                'SELECT budget_id, spend FROM "LiteLLM_TeamMembership" '
                "WHERE team_id = :tid AND user_id = :uid"
            ),
            {"tid": team_id, "uid": user_id},
        )
    ).mappings().first()
    if membership is None:
        return None

    team = (
        await litellm_db.execute(
            text('SELECT team_alias, metadata FROM "LiteLLM_TeamTable" WHERE team_id = :tid'),
            {"tid": team_id},
        )
    ).mappings().first()
    team_alias = team["team_alias"] if team else team_id

    budget_id = membership["budget_id"]
    if not budget_id and team and isinstance(team["metadata"], dict):
        budget_id = team["metadata"].get("team_member_budget_id")

    max_budget = budget_duration = reset_at = None
    if budget_id:
        row = (
            await litellm_db.execute(
                text(
                    "SELECT max_budget, budget_duration, budget_reset_at "
                    'FROM "LiteLLM_BudgetTable" WHERE budget_id = :bid'
                ),
                {"bid": budget_id},
            )
        ).mappings().first()
        if row:
            max_budget = float(row["max_budget"]) if row["max_budget"] is not None else None
            budget_duration = row["budget_duration"]
            reset_at = row["budget_reset_at"].isoformat() if row["budget_reset_at"] else None

    spend = float(membership["spend"]) if membership["spend"] is not None else 0.0
    remaining = (max_budget - spend) if max_budget is not None else None
    return {
        "team_id": team_id,
        "team_alias": team_alias,
        "max_budget": max_budget,
        "spend": spend,
        "remaining": remaining,
        "budget_duration": budget_duration,
        "reset_at": reset_at,
    }
