"""Authorization helpers for team-level and global permissions."""

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.custom_user import CustomUser, GlobalRole


async def get_team_access(user: CustomUser, team_id: str, db: AsyncSession) -> str:
    """The caller's access level for a team: "admin" (team admin or super user)
    or "member". Raises 403 for everyone else.

    Membership is recognised from the TeamTable.admins/members arrays and, as a
    fallback, from UserTable.teams — the same source ``list_my_teams`` uses. The
    two can diverge (a user added outside the portal is in UserTable.teams but
    may be absent from the TeamTable.members array); without the fallback a
    legitimate member seen under "My Teams" gets a false 403 on the usage tab.
    """
    if user.global_role == GlobalRole.SUPER_USER:
        return "admin"

    result = await db.execute(
        text('SELECT admins, members FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    if row:
        if user.user_id in list(row["admins"] or []):
            return "admin"
        if user.user_id in list(row["members"] or []):
            return "member"

    # Fallback: the user may be a member via UserTable.teams (what "My Teams"
    # lists) even when the TeamTable.members array is stale/empty.
    in_my_teams = (
        await db.execute(
            text(
                'SELECT 1 FROM "LiteLLM_UserTable" '
                "WHERE user_id = :user_id AND :team_id = ANY(teams)"
            ),
            {"user_id": user.user_id, "team_id": team_id},
        )
    ).first()
    if in_my_teams is not None:
        return "member"

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You are not a member of team {team_id}",
    )


async def require_team_admin(user: CustomUser, team_id: str, db: AsyncSession) -> None:
    """Verify the user is an admin of the specified team or a super user."""
    if user.global_role == GlobalRole.SUPER_USER:
        return

    result = await db.execute(
        text('SELECT admins FROM "LiteLLM_TeamTable" WHERE team_id = :team_id'),
        {"team_id": team_id},
    )
    row = result.mappings().first()
    admins = list(row["admins"] or []) if row else []
    if user.user_id not in admins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You are not an admin of team {team_id}",
        )
