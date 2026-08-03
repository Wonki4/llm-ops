"""Tool logic (transport-independent): authorization + data assembly.

Kept out of main.py so it can be unit-tested without importing the MCP SDK.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.data import list_visible_teams, member_budget_usage


class NotVisibleError(Exception):
    """The caller may not see this team (not a member, or strictly hidden)."""


async def list_my_teams_logic(portal_db: AsyncSession, litellm_db: AsyncSession, user_id: str) -> dict:
    return {"teams": await list_visible_teams(portal_db, litellm_db, user_id)}


async def team_budget_usage_logic(
    portal_db: AsyncSession, litellm_db: AsyncSession, user_id: str, team_id: str
) -> dict:
    """The caller's own in-team budget/spend, authorized against the visible set
    (member AND not strictly hidden). Raises NotVisibleError otherwise — the same
    message whether the team is hidden or doesn't exist (no existence leak)."""
    visible = {t["team_id"] for t in await list_visible_teams(portal_db, litellm_db, user_id)}
    if team_id not in visible:
        raise NotVisibleError("not a member of this team, or team not visible")
    usage = await member_budget_usage(litellm_db, team_id, user_id)
    if usage is None:
        raise NotVisibleError("not a member of this team, or team not visible")
    return usage
